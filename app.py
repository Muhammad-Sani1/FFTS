import os
from contextlib import contextmanager
import sys
import uuid
import json
import logging
from time import sleep
from datetime import datetime, timedelta
from dateutil.parser import parse
from flask import Flask, render_template, request, redirect, url_for, flash, session, abort
from flask_wtf import FlaskForm
from wtforms import StringField, FloatField, SelectField, TextAreaField, EmailField, SubmitField, BooleanField
from wtforms.validators import DataRequired, Email, Optional, NumberRange, EqualTo
from flask_mail import Mail, Message
from smtplib import SMTPException, SMTPAuthenticationError
import gspread
from google.oauth2.service_account import Credentials
from dateutil.parser import parse
import numpy as np
import plotly.graph_objects as go
import plotly.io as pio
from flask_caching import Cache
from math import ceil
from translations import translations

# Configure logging
logging.basicConfig(stream=sys.stdout, level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError as e:
    logger.error("Failed to load environment variables: %s", e)
    sys.exit(1)

# Initialize Flask app
app = Flask(__name__, template_folder='templates', static_folder='static')
app_secret_key = os.environ.get('FLASK_SECRET_KEY')
if not app_secret_key:
    logger.error("FLASK_SECRET_KEY environment variable not set")
    sys.exit(1)
app.secret_key = app_secret_key
app.config['WTF_CSRF_ENABLED'] = True

# Configure Flask-Caching
cache_config = {
    "CACHE_TYPE": "SimpleCache",
    "CACHE_DEFAULT_TIMEOUT": 300
}
app.config.from_mapping(cache_config)
cache = Cache(app)

# Configure Flask-Mail
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'ficore.ai.africa@gmail.com'
app.config['MAIL_PASSWORD'] = os.environ.get('SMTP_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = 'ficore.ai.africa@gmail.com'
app.config['MAIL_ENABLED'] = bool(app.config['MAIL_PASSWORD'])
if not app.config['MAIL_ENABLED']:
    logger.warning("SMTP_PASSWORD not set in environment. Email functionality will be disabled.")
try:
    mail = Mail(app)
    logger.info("Flask-Mail initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize Flask-Mail: {str(e)}")

scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
sheets = None

def initialize_sheets(max_retries=3, backoff_factor=2):
    global sheets
    for attempt in range(max_retries):
        try:
            creds_json = os.environ.get('GOOGLE_CREDENTIALS_JSON')
            if not creds_json:
                logger.error("GOOGLE_CREDENTIALS_JSON environment variable not set")
                return
            creds_dict = json.loads(creds_json)
            creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
            client = gspread.authorize(creds)
            sheets = client.open_by_key('13hbiMTMRBHo9MHjWwcugngY_aSiuxII67HCf03MiZ8I')
            logger.info("Successfully initialized Google Sheets")
            return
        except json.JSONDecodeError as e:
            logger.error(f"Invalid GOOGLE_CREDENTIALS_JSON format: {e}")
            return
        except gspread.exceptions.APIError as e:
            logger.error(f"Google Sheets API error: {e}")
            return
        except Exception as e:
            logger.error(f"Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                sleep(backoff_factor ** attempt)
            else:
                logger.error("Max retries exceeded")
                return

initialize_sheets()

# Worksheet configurations with standardized snake_case headers
WORKSHEETS = {
    'Authentication': {
        'name': 'AuthenticationSheet',
        'headers': ['timestamp', 'first_name', 'email', 'last_name', 'phone_number', 'language']
    },
    'HealthScore': {
        'name': 'HealthScoreSheet',
        'headers': ['timestamp', 'business_name', 'monthly_income', 'monthly_expenses', 'debt_loan', 'debt_interest_rate', 'auto_email', 'phone_number', 'first_name', 'last_name', 'user_type', 'email', 'id', 'badges', 'language', 'score']
    },
    'NetWorth': {
        'name': 'NetWorthSheet',
        'headers': ['id', 'timestamp', 'first_name', 'email', 'language', 'assets', 'liabilities', 'net_worth']
    },
    'Quiz': {
        'name': 'QuizSheet',
        'headers': ['timestamp', 'first_name', 'email', 'language', 'q1', 'q2', 'q3', 'q4', 'q5', 'q6', 'q7', 'q8', 'q9', 'q10', 'quiz_score', 'personality', 'auto_email']
    },
    'EmergencyFund': {
        'name': 'EmergencyFundSheet',
        'headers': ['timestamp', 'first_name', 'email', 'language', 'monthly_expenses', 'recommended_fund', 'auto_email']
    },
    'Budget': {
        'name': 'BudgetSheet',
        'headers': ['timestamp', 'first_name', 'email', 'confirm_email', 'auto_email', 'language', 'monthly_income', 'housing_expenses', 'food_expenses', 'transport_expenses', 'other_expenses', 'total_expenses', 'savings', 'surplus_deficit', 'rank', 'total_users', 'badges']
    },
    'ExpenseTracker': {
        'name': 'ExpenseTrackerSheet',
        'headers': ['id', 'email', 'amount', 'category', 'date', 'description', 'timestamp', 'transaction_type', 'running_balance', 'first_name', 'language', 'auto_email']
    },
    'BillPlanner': {
        'name': 'BillPlannerSheet',
        'headers': ['timestamp', 'first_name', 'email', 'language', 'description', 'amount', 'due_date', 'category', 'recurrence', 'status', 'auto_email']
    }
}

def initialize_worksheet(tool):
    if sheets is None:
        logger.error(f"Cannot initialize worksheet {tool}: Google Sheets not initialized")
        return None
    config = WORKSHEETS[tool]
    try:
        sheet = sheets.worksheet(config['name'])
    except gspread.exceptions.WorksheetNotFound:
        logger.info(f"Creating new worksheet: {config['name']}")
        sheet = sheets.add_worksheet(title=config['name'], rows=100, cols=len(config['headers']))
        sheet.append_row(config['headers'])
    try:
        current_headers = sheet.row_values(1)
        if not current_headers or current_headers != config['headers']:
            logger.info(f"Updating headers for {config['name']}")
            sheet.clear()
            sheet.append_row(config['headers'])
        excluded_headers = ['timestamp', 'badges', 'score', 'running_balance', 'surplus_deficit', 'total_expenses', 'savings', 'rank', 'total_users', 'recommended_fund', 'net_worth', 'quiz_score', 'personality']
        for header in current_headers:
            if header.lower() not in [h.lower() for h in config['headers']] and header.lower() not in [eh.lower() for eh in excluded_headers]:
                logger.warning(f"Unexpected header '{header}' in {config['name']}")
    except Exception as e:
        logger.error(f"Error setting headers for {config['name']}: {e}")
        sheet.clear()
        sheet.append_row(config['headers'])
    return sheet

# Utility function to parse numbers
def parse_number(value):
    try:
        if isinstance(value, str):
            value = value.replace(',', '')
        return float(value)
    except (ValueError, TypeError):
        return 0

# Translation utility
def get_translation(key, language='English'):
    try:
        return translations.get(language, translations['English'])[key]
    except KeyError:
        logger.warning(f"Translation key '{key}' not found for language '{language}', falling back to English")
        return translations['English'].get(key, f"Missing translation: {key}")

# Store authentication data
def store_authentication_data(form_data):
    language = session.get('language', 'English')
    try:
        auth_sheet = initialize_worksheet('Authentication')
        if auth_sheet is None:
            logger.error("Authentication worksheet not available")
            flash(get_translation('Failed to store authentication data', language), 'error')
            return
        auth_data = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'first_name': form_data.get('first_name', ''),
            'email': form_data.get('email', ''),
            'last_name': form_data.get('last_name', ''),
            'phone_number': form_data.get('phone', ''),
            'language': form_data.get('language', language)
        }
        update_or_append_user_data(auth_data, 'Authentication')
    except Exception as e:
        logger.error(f"Error storing authentication data: {e}")
        flash(get_translation('Failed to store authentication data', language), 'error')

# Fetch user data by email
def get_user_data_by_email(email, tool):
    language = session.get('language', 'English')
    try:
        sheet = initialize_worksheet(tool)
        if sheet is None:
            logger.error(f"Cannot fetch data from {tool}: Worksheet not initialized")
            return []
        records = sheet.get_all_records()
        user_records = []
        for record in records:
            if not isinstance(record, dict):
                logger.warning(f"Malformed record in {tool}: {record}")
                continue
            if record.get('email') == email:
                user_records.append(record)
        return user_records
    except gspread.exceptions.APIError as e:
        logger.error(f"Google Sheets API error fetching data from {WORKSHEETS[tool]['name']}: {e}")
        flash(get_translation('Failed to fetch data due to Google Sheets API limit', language), 'error')
        return []
    except Exception as e:
        logger.error(f"Error fetching user data from {WORKSHEETS[tool]['name']}: {e}")
        flash(get_translation('Failed to fetch data due to server error', language), 'error')
        return []

# Fetch record by ID
def get_record_by_id(id, tool):
    language = session.get('language', 'English')
    try:
        sheet = initialize_worksheet(tool)
        if sheet is None:
            logger.error(f"Cannot fetch record from {tool}: Worksheet not initialized")
            return None
        records = sheet.get_all_records()
        for record in records:
            if not isinstance(record, dict):
                logger.warning(f"Malformed record in {tool}: {record}")
                continue
            if record.get('id') == id or record.get('timestamp') == id:
                return record
        return None
    except gspread.exceptions.APIError as e:
        logger.error(f"Google Sheets API error fetching record from {WORKSHEETS[tool]['name']}: {e}")
        flash(get_translation('Failed to fetch record due to Google Sheets API limit', language), 'error')
        return None
    except Exception as e:
        logger.error(f"Error fetching record by ID from {WORKSHEETS[tool]['name']}: {e}")
        flash(get_translation('Failed to fetch record due to server error', language), 'error')
        return None

# Update or append user data
def update_or_append_user_data(user_data, tool, update_only_specific_fields=None):
    language = session.get('language', 'English')
    sheet = initialize_worksheet(tool)
    if sheet is None:
        logger.error(f"Cannot update/append data to {tool}: Worksheet not initialized")
        flash(get_translation('Failed to save data due to Google Sheets initialization error', language), 'error')
        return
    headers = WORKSHEETS[tool]['headers']
    try:
        records = sheet.get_all_records()
        email = user_data.get('email')
        id = user_data.get('id') or user_data.get('timestamp')
        found = False
        for i, record in enumerate(records, start=2):
            if not isinstance(record, dict):
                logger.warning(f"Malformed record in {tool}: {record}")
                continue
            if record.get('email') == email or record.get('id') == id or record.get('timestamp') == id:
                if update_only_specific_fields:
                    merged_data = {**record}
                    for field in update_only_specific_fields:
                        if field in user_data:
                            merged_data[field] = user_data[field]
                else:
                    merged_data = {**record, **user_data}
                sheet.update(f'A{i}:{chr(64 + len(headers))}{i}', [[merged_data.get(header, '') for header in headers]])
                found = True
                break
        if not found:
            sheet.append_row([user_data.get(header, '') for header in headers])
    except gspread.exceptions.APIError as e:
        logger.error(f"Google Sheets API error updating/appending data to {WORKSHEETS[tool]['name']}: {e}")
        flash(get_translation('Failed to save data due to Google Sheets API limit', language), 'error')
    except Exception as e:
        logger.error(f"Error updating/appending data to {WORKSHEETS[tool]['name']}: {e}")
        flash(get_translation('Failed to save data due to server error', language), 'error')

# Calculate running balance
def calculate_running_balance(email):
    language = session.get('language', 'English')
    try:
        sheet = initialize_worksheet('ExpenseTracker')
        if sheet is None:
            logger.error("Cannot calculate running balance: ExpenseTracker worksheet not initialized")
            flash(get_translation('Failed to calculate running balance due to Google Sheets error', language), 'error')
            return 0
        records = sheet.get_all_records()
        user_records = [r for r in records if r.get('email') == email]
        if not user_records:
            return 0
        sorted_records = sorted(user_records, key=lambda x: parse(x.get('timestamp', '1970-01-01 00:00:00')))
        balance = 0
        for i, record in enumerate(sorted_records):
            amount = parse_number(record.get('amount', 0))
            balance += amount if record.get('transaction_type') == 'Income' else -amount
            if i == len(sorted_records) - 1:
                record['running_balance'] = balance
                update_or_append_user_data(record, 'ExpenseTracker', update_only_specific_fields=['running_balance'])
        return balance
    except gspread.exceptions.APIError as e:
        logger.error(f"Google Sheets API error calculating running balance: {e}")
        flash(get_translation('Failed to calculate running balance due to Google Sheets API limit', language), 'error')
        return 0
    except Exception as e:
        logger.error(f"Error calculating running balance: {e}")
        flash(get_translation('Failed to calculate running balance due to server error', language), 'error')
        return 0

# Parse bill data
def parse_bill_data(email, language='English'):
    try:
        sheet = initialize_worksheet('BillPlanner')
        if sheet is None:
            logger.error("Cannot parse bill data: BillPlanner worksheet not initialized")
            flash(get_translation('Failed to fetch bill data due to Google Sheets error', language), 'error')
            return []
        records = sheet.get_all_records()
        user_records = [r for r in records if r.get('email') == email]
        parsed_records = []
        for record in user_records:
            parsed_record = {
                'timestamp': record.get('timestamp', ''),
                'description': record.get('description', ''),
                'amount': parse_number(record.get('amount', 0)),
                'due_date': record.get('due_date', ''),
                'category': record.get('category', 'Other'),
                'recurrence': record.get('recurrence', 'None'),
                'status': record.get('status', 'Pending'),
                'first_name': record.get('first_name', ''),
                'language': record.get('language', language),
                'auto_email': record.get('auto_email', '').lower() == 'true'
            }
            parsed_records.append(parsed_record)
        return parsed_records
    except gspread.exceptions.APIError as e:
        logger.error(f"Google Sheets API error parsing bill data: {e}")
        flash(get_translation('Failed to fetch bill data due to Google Sheets API limit', language), 'error')
        return []
    except Exception as e:
        logger.error(f"Error parsing bill data: {e}")
        flash(get_translation('Failed to fetch bill data due to server error', language), 'error')
        return []

# Generate bill schedule for recurring bills
def generate_bill_schedule(bills, start_date, end_date, language='English'):
    try:
        start = parse(start_date)
        end = parse(end_date)
        schedule = []
        for bill in bills:
            if bill['status'] != 'Pending':
                continue
            due_date = parse(bill['due_date'])
            if bill['recurrence'] == 'None':
                if start <= due_date <= end:
                    schedule.append(bill)
            else:
                current_date = due_date
                recurrence_map = {
                    'Daily': timedelta(days=1),
                    'Weekly': timedelta(weeks=1),
                    'Monthly': timedelta(days=30),
                    'Yearly': timedelta(days=365)
                }
                delta = recurrence_map.get(bill['recurrence'], timedelta(days=0))
                while current_date <= end:
                    if current_date >= start:
                        scheduled_bill = bill.copy()
                        scheduled_bill['due_date'] = current_date.strftime('%Y-%m-%d')
                        schedule.append(scheduled_bill)
                    current_date += delta
        return sorted(schedule, key=lambda x: parse(x['due_date']))
    except ValueError as e:
        logger.error(f"Invalid date format in bill schedule: {e}")
        flash(get_translation('Invalid date format in bill schedule', language), 'error')
        return []
    except Exception as e:
        logger.error(f"Error generating bill schedule: {e}")
        flash(get_translation('Failed to generate bill schedule due to server error', language), 'error')
        return []

# Parse expense data
def parse_expense_data(email, language='English'):
    try:
        sheet = initialize_worksheet('ExpenseTracker')
        if sheet is None:
            logger.error("Cannot parse expense data: ExpenseTracker worksheet not initialized")
            return []
        records = sheet.get_all_records()
        user_records = [r for r in records if r.get('email') == email]
        parsed_records = []
        for record in user_records:
            parsed_record = {
                'id': record.get('id', ''),
                'amount': parse_number(record.get('amount', 0)),
                'category': record.get('category', 'Other'),
                'date': record.get('date', ''),
                'description': record.get('description', ''),
                'transaction_type': record.get('transaction_type', 'Expense'),
                'running_balance': parse_number(record.get('running_balance', 0))
            }
            parsed_records.append(parsed_record)
        return parsed_records
    except gspread.exceptions.APIError as e:
        logger.error(f"Google Sheets API error parsing expense data: {e}")
        flash(get_translation('Failed to fetch expense data due to Google Sheets API limit', language), 'error')
        return []
    except Exception as e:
        logger.error(f"Error parsing expense data: {e}")
        flash(get_translation('Failed to fetch expense data due to server error', language), 'error')
        return []

# Summarize expenses
def summarize_expenses(expenses, language='English'):
    try:
        summary = {
            'total_income': 0.0,
            'total_expenses': 0.0,
            'net_balance': 0.0,
            'by_category': {}
        }
        for expense in expenses:
            amount = expense['amount']
            category = expense['category']
            transaction_type = expense['transaction_type']
            if transaction_type == 'Income':
                summary['total_income'] += amount
            else:
                summary['total_expenses'] += amount
            summary['by_category'][category] = summary['by_category'].get(category, 0) + (
                amount if transaction_type == 'Income' else -amount
            )
        summary['net_balance'] = summary['total_income'] - summary['total_expenses']
        translated_summary = {
            'total_income': summary['total_income'],
            'total_expenses': summary['total_expenses'],
            'net_balance': summary['net_balance'],
            'by_category': {
                get_translation(k, language): v for k, v in summary['by_category'].items()
            }
        }
        return translated_summary
    except Exception as e:
        logger.error(f"Error summarizing expenses: {e}")
        flash(get_translation('Failed to summarize expenses due to server error', language), 'error')
        return {
            'total_income': 0.0,
            'total_expenses': 0.0,
            'net_balance': 0.0,
            'by_category': {}
        }

# Generate expense charts
@cache.memoize(timeout=300)
def generate_expense_charts(email, language='English'):
    try:
        sheet = initialize_worksheet('ExpenseTracker')
        if sheet is None:
            logger.error("Cannot generate expense charts: ExpenseTracker worksheet not initialized")
            return get_translation('No expense data available.', language)
        records = sheet.get_all_records()
        user_records = [r for r in records if r.get('email') == email]
        categories = {}
        for record in user_records:
            category = record.get('category', 'Other')
            amount = parse_number(record.get('amount', 0))
            transaction_type = record.get('transaction_type', 'Expense')
            sign = 1 if transaction_type == 'Income' else -1
            categories[category] = categories.get(category, 0) + (sign * amount)
        labels = list(categories.keys())
        values = [abs(v) for v in categories.values()]
        if not labels:
            return get_translation('No expense data available.', language)
        pie_fig = go.Figure(data=[go.Pie(labels=labels, values=values, hole=0.3, marker=dict(colors=['#2E7D32', '#DC3545', '#0288D1', '#FFB300', '#4CAF50', '#9C27B0']))])
        pie_fig.update_layout(
            title=get_translation('Expense Breakdown by Category', language),
            showlegend=True,
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font=dict(size=12),
            hovermode='closest'
        )
        chart_html = pio.to_html(pie_fig, full_html=False, include_plotlyjs=True)
        return chart_html
    except gspread.exceptions.APIError as e:
        logger.error(f"Google Sheets API error generating expense charts: {e}")
        flash(get_translation('Failed to generate charts due to Google Sheets API limit', language), 'error')
        return get_translation('Chart failed to load. Please try again.', language)
    except Exception as e:
        logger.error(f"Error generating expense charts: {e}")
        flash(get_translation('Failed to generate charts due to server error', language), 'error')
        return get_translation('Chart failed to load. Please try again.', language)

# Form definitions
class HealthScoreForm(FlaskForm):
    first_name = StringField('First Name', validators=[DataRequired()], render_kw={'placeholder': 'Enter your first name', 'aria-label': 'Your first name', 'data-tooltip': 'Your first name, like John or Aisha.'})
    last_name = StringField('Last Name', validators=[Optional()], render_kw={'placeholder': 'Enter your last name (optional)', 'aria-label': 'Your last name', 'data-tooltip': 'Your last name, like Okeke or Musa (you can skip this).'})
    email = EmailField('Email', validators=[DataRequired(), Email()], render_kw={'placeholder': 'Enter your email', 'aria-label': 'Your email address', 'data-tooltip': 'Your email, like example@gmail.com, to get your score.'})
    confirm_email = EmailField('Confirm Email', validators=[DataRequired(), Email(), EqualTo('email', message='Emails must match')], render_kw={'placeholder': 'Re-enter your email', 'aria-label': 'Confirm your email address', 'data-tooltip': 'Type your email again to make sure it’s correct.'})
    phone_number = StringField('Phone Number', validators=[Optional()], render_kw={'placeholder': 'Enter your phone number (optional)', 'aria-label': 'Your phone number', 'data-tooltip': 'Your mobile number, like 08012345678 (you can skip this).'})
    language = SelectField('Language', choices=[('English', 'English'), ('Hausa', 'Hausa')], validators=[DataRequired()], render_kw={'aria-label': 'Select your language', 'data-tooltip': 'Choose English or Hausa for the form.'})
    business_name = StringField('Business Name', validators=[DataRequired()], render_kw={'placeholder': 'Type personal name if no business', 'aria-label': 'Your business or personal name', 'data-tooltip': 'Name of your business, or your name if you don’t have a business.'})
    user_type = SelectField('User Type', choices=[('Individual', 'Individual'), ('Business', 'Business')], validators=[DataRequired()], render_kw={'aria-label': 'Are you an individual or business?', 'data-tooltip': 'Choose Individual if it’s just you, or Business if you have a shop or company.'})
    monthly_income = FloatField('Income/Revenue (₦)', validators=[DataRequired(), NumberRange(min=0, max=10000000000)], render_kw={'placeholder': 'e.g. 150,000', 'aria-label': 'Money you get every month', 'data-tooltip': 'All money you receive, like salary, sales from your shop, gifts, or side jobs.'})
    monthly_expenses = FloatField('Expenses/Costs (₦)', validators=[DataRequired(), NumberRange(min=0, max=10000000000)], render_kw={'placeholder': 'e.g. 60,000', 'aria-label': 'Money you spend every month', 'data-tooltip': 'Money you spend on things like food, rent, transport, bills, or taxes.'})
    debt_loan = FloatField('Debt/Loan (₦)', validators=[DataRequired(), NumberRange(min=0, max=10000000000)], render_kw={'placeholder': 'e.g. 25,000', 'aria-label': 'Money you owe', 'data-tooltip': 'Money you borrowed from friends, family, or a bank loan you need to pay back.'})
    debt_interest_rate = FloatField('Interest Percentage on Debt (%)', validators=[Optional(), NumberRange(min=0, max=100)], render_kw={'placeholder': 'e.g. 10%', 'aria-label': 'Extra percentage on money you owe', 'data-tooltip': 'Extra percentage you pay on a bank loan or borrowing, like 10% (you can skip this if you don’t know).'})
    auto_email = BooleanField('Send Me My Score by Email', default=False, render_kw={'aria-label': 'Send score by email', 'data-tooltip': 'Check this to get your financial health score sent to your email.'})
    record_id = SelectField('Select Record to Edit', choices=[('', 'Create New Record')], validators=[Optional()], render_kw={'aria-label': 'Select a previous record', 'data-tooltip': 'Choose a previous form you filled to edit it, or select "Create New Record" for a new one.'})
    submit = SubmitField('Submit', render_kw={'aria-label': 'Submit your financial information'})

class NetWorthForm(FlaskForm):
    first_name = StringField('First Name', validators=[DataRequired()], render_kw={'placeholder': 'e.g. John', 'aria-label': 'First Name', 'data-tooltip': 'Enter your first name.'})
    email = EmailField('Email', validators=[DataRequired(), Email()], render_kw={'placeholder': 'e.g. john.doe@example.com', 'aria-label': 'Email', 'data-tooltip': 'Enter your email address.'})
    language = SelectField('Language', choices=[('English', 'English'), ('Hausa', 'Hausa')], validators=[DataRequired()], render_kw={'aria-label': 'Language', 'data-tooltip': 'Select your preferred language.'})
    assets = FloatField('Total Assets (₦)', validators=[DataRequired(), NumberRange(min=0, max=10000000000)], render_kw={'placeholder': 'e.g. ₦500,000', 'aria-label': 'Total Assets', 'data-tooltip': 'Enter the total value of your assets.'})
    liabilities = FloatField('Total Liabilities (₦)', validators=[DataRequired(), NumberRange(min=0, max=10000000000)], render_kw={'placeholder': 'e.g. ₦200,000', 'aria-label': 'Total Liabilities', 'data-tooltip': 'Enter the total value of your liabilities.'})
    record_id = SelectField('Select Record to Edit', choices=[('', 'Create New Record')], validators=[Optional()], render_kw={'aria-label': 'Select Record', 'data-tooltip': 'Select a previous record to edit or create a new one.'})
    submit = SubmitField('Get My Net Worth', render_kw={'aria-label': 'Submit Net Worth Form'})

class QuizForm(FlaskForm):
    first_name = StringField('First Name', validators=[DataRequired()], render_kw={'placeholder': 'e.g. John', 'aria-label': 'First Name', 'data-tooltip': 'Enter your first name.'})
    email = EmailField('Email', validators=[DataRequired(), Email()], render_kw={'placeholder': 'e.g. john.doe@example.com', 'aria-label': 'Email', 'data-tooltip': 'Enter your email address.'})
    language = SelectField('Language', choices=[('English', 'English'), ('Hausa', 'Hausa')], validators=[DataRequired()], render_kw={'aria-label': 'Language', 'data-tooltip': 'Select your preferred language.'})
    for i in range(1, 11):
        locals()[f'q{i}'] = SelectField(f'Question {i}', choices=[('Yes', 'Yes'), ('No', 'No')], validators=[DataRequired()], render_kw={'aria-label': f'Question {i}', 'data-tooltip': 'Answer with Yes or No based on your financial habits.'})
    auto_email = BooleanField('Send Email Notification', default=False, render_kw={'aria-label': 'Send Email Notification', 'data-tooltip': 'Check to receive email notifications.'})
    record_id = SelectField('Select Record to Edit', choices=[('', 'Create New Record')], validators=[Optional()], render_kw={'aria-label': 'Select Record', 'data-tooltip': 'Select a previous record to edit or create a new one.'})
    submit = SubmitField('Submit Quiz', render_kw={'aria-label': 'Submit Quiz Form'})

class BudgetForm(FlaskForm):
    def __init__(self, language='English', *args, **kwargs):
        super(BudgetForm, self).__init__(*args, **kwargs)
        self.language = language
        t = translations.get(self.language, translations['English'])
        self.first_name.label.text = t['First Name']
        self.email.label.text = t['Email']
        self.confirm_email.label.text = t['Confirm Email']
        self.language.label.text = t['Language']
        self.monthly_income.label.text = t['Total Monthly Income']
        self.housing_expenses.label.text = t['Housing Expenses']
        self.food_expenses.label.text = t['Food Expenses']
        self.transport_expenses.label.text = t['Transport Expenses']
        self.other_expenses.label.text = t['Other Expenses']
        self.auto_email.label.text = t['Send Email Notification']
        self.record_id.label.text = t['Select Record to Edit']
        self.submit.label.text = t['Plan My Budget']
        self.first_name.render_kw['data-tooltip'] = t['Enter your first name.']
        self.email.render_kw['data-tooltip'] = t['Enter your email address.']
        self.confirm_email.render_kw['data-tooltip'] = t['Re-enter your email to confirm.']
        self.language.render_kw['data-tooltip'] = t['Select your preferred language.']
        self.monthly_income.render_kw['data-tooltip'] = t['Enter your monthly income.']
        self.housing_expenses.render_kw['data-tooltip'] = t['Enter your housing expenses.']
        self.food_expenses.render_kw['data-tooltip'] = t['Enter your food expenses.']
        self.transport_expenses.render_kw['data-tooltip'] = t['Enter your transport expenses.']
        self.other_expenses.render_kw['data-tooltip'] = t['Enter your other expenses.']
        self.auto_email.render_kw['data-tooltip'] = t['Check to receive email report.']
        self.record_id.render_kw['data-tooltip'] = t['Select a previous record or create new.']
        self.monthly_income.render_kw['placeholder'] = t['e.g. ₦150,000']
        self.housing_expenses.render_kw['placeholder'] = t['e.g. ₦50,000']
        self.food_expenses.render_kw['placeholder'] = t['e.g. ₦30,000']
        self.transport_expenses.render_kw['placeholder'] = t['e.g. ₦20,000']
        self.other_expenses.render_kw['placeholder'] = t['e.g. ₦10,000']
        self.record_id.choices = [('', t['Create New Record'])]

    def validate_two_decimals(form, field):
        if field.data is not None:
            if not str(float(field.data)).endswith('.0') and len(str(float(field.data)).split('.')[-1]) > 2:
                raise ValidationError(translations.get(form.language, translations['English'])['Two decimal places required'])

    first_name = StringField(validators=[DataRequired()], render_kw={'placeholder': 'e.g. John', 'aria-label': 'First Name'})
    email = EmailField(validators=[DataRequired(), Email()], render_kw={'placeholder': 'e.g. john.doe@example.com', 'aria-label': 'Email'})
    confirm_email = EmailField(validators=[DataRequired(), Email(), EqualTo('email', message=lambda form, field: translations.get(form.language, translations['English'])['Emails must match'])], render_kw={'placeholder': 'e.g. john.doe@example.com', 'aria-label': 'Confirm Email'})
    language = SelectField(choices=[('English', 'English'), ('Hausa', 'Hausa')], validators=[DataRequired()], render_kw={'aria-label': 'Language'})
    monthly_income = FloatField(validators=[DataRequired(), NumberRange(min=0, max=100000000), validate_two_decimals], render_kw={'placeholder': 'e.g. ₦150,000', 'aria-label': 'Monthly Income'})
    housing_expenses = FloatField(validators=[DataRequired(), NumberRange(min=0, max=100000000), validate_two_decimals], render_kw={'placeholder': 'e.g. ₦50,000', 'aria-label': 'Housing Expenses'})
    food_expenses = FloatField(validators=[DataRequired(), NumberRange(min=0, max=100000000), validate_two_decimals], render_kw={'placeholder': 'e.g. ₦30,000', 'aria-label': 'Food Expenses'})
    transport_expenses = FloatField(validators=[DataRequired(), NumberRange(min=0, max=100000000), validate_two_decimals], render_kw={'placeholder': 'e.g. ₦20,000', 'aria-label': 'Transport Expenses'})
    other_expenses = FloatField(validators=[DataRequired(), NumberRange(min=0, max=100000000), validate_two_decimals], render_kw={'placeholder': 'e.g. ₦10,000', 'aria-label': 'Other Expenses'})
    auto_email = BooleanField(default=False, render_kw={'aria-label': 'Send Email Report'})
    record_id = SelectField(choices=[('', 'Create New Record')], validators=[Optional()], render_kw={'aria-label': 'Select Record'})
    submit = SubmitField(render_kw={'aria-label': 'Submit Budget Form'})

class ExpenseTrackerForm(FlaskForm):
    def __init__(self, language='English', *args, **kwargs):
        super(ExpenseTrackerForm, self).__init__(*args, **kwargs)
        self.language = language
        t = translations.get(self.language, translations['English'])
        self.first_name.label.text = t['First Name']
        self.email.label.text = t['Email']
        self.confirm_email.label.text = t['Confirm Email']
        self.language.label.text = t['Language']
        self.amount.label.text = t['Amount']
        self.description.label.text = t['Description']
        self.category.label.text = t['Category']
        self.transaction_type.label.text = t['Transaction Type']
        self.date.label.text = t['Date']
        self.auto_email.label.text = t['Send Email Notification']
        self.record_id.label.text = t['Select Record to Edit']
        self.submit.label.text = t['Add Transaction']
        self.first_name.render_kw['data-tooltip'] = t['Enter your first name.']
        self.email.render_kw['data-tooltip'] = t['Enter your email address.']
        self.confirm_email.render_kw['data-tooltip'] = t['Re-enter your email to confirm.']
        self.language.render_kw['data-tooltip'] = t['Select your preferred language.']
        self.amount.render_kw['data-tooltip'] = t['Enter the transaction amount.']
        self.description.render_kw['data-tooltip'] = t['Describe the transaction.']
        self.category.render_kw['data-tooltip'] = t['Select the transaction category.']
        self.transaction_type.render_kw['data-tooltip'] = t['Select if this is income or expense.']
        self.date.render_kw['data-tooltip'] = t['Enter the transaction date (YYYY-MM-DD).']
        self.auto_email.render_kw['data-tooltip'] = t['Check to receive email notifications.']
        self.record_id.render_kw['data-tooltip'] = t['Select a previous record to edit or create a new one.']
        self.amount.render_kw['placeholder'] = t['e.g. ₦5,000']
        self.description.render_kw['placeholder'] = t['e.g. Grocery shopping']
        self.date.render_kw['placeholder'] = t['e.g. 2025-06-01']
        self.record_id.choices = [('', t['Create New Record'])]

    def validate_two_decimals(form, field):
        if field.data is not None:
            if not str(float(field.data)).endswith('.0') and len(str(float(field.data)).split('.')[-1]) > 2:
                raise ValidationError(translations.get(form.language, translations['English'])['Two decimal places required'])

    def validate_date_format(form, field):
        try:
            parse(field.data)
        except ValueError:
            raise ValidationError(translations.get(form.language, translations['English'])['Date must be in YYYY-MM-DD format.'])

    first_name = StringField(validators=[DataRequired()], render_kw={'placeholder': 'e.g. John', 'aria-label': 'First Name'})
    email = EmailField(validators=[DataRequired(), Email()], render_kw={'placeholder': 'e.g. john.doe@example.com', 'aria-label': 'Email'})
    confirm_email = EmailField(validators=[DataRequired(), Email(), EqualTo('email', message=lambda form, field: translations.get(form.language, translations['English'])['Emails must match'])], render_kw={'placeholder': 'e.g. john.doe@example.com', 'aria-label': 'Confirm Email'})
    language = SelectField(choices=[('English', 'English'), ('Hausa', 'Hausa')], validators=[DataRequired()], render_kw={'aria-label': 'Language'})
    amount = FloatField(validators=[DataRequired(), NumberRange(min=0, max=10000000000), validate_two_decimals], render_kw={'placeholder': 'e.g. ₦5,000', 'aria-label': 'Amount'})
    description = TextAreaField(validators=[DataRequired()], render_kw={'placeholder': 'e.g. Grocery shopping', 'aria-label': 'Description'})
    category = SelectField(choices=[
        ('Food and Groceries', 'Food and Groceries'),
        ('Transport', 'Transport'),
        ('Housing', 'Housing'),
        ('Utilities', 'UtilitiesVital'),
        ('Entertainment', 'Entertainment'),
        ('Other', 'Other')
    ], validators=[DataRequired()], render_kw={'aria-label': 'Category'})
    transaction_type = SelectField(choices=[('Income', 'Income'), ('Expense', 'Expense')], validators=[DataRequired()], render_kw={'aria-label': 'Transaction Type'})
    date = StringField(validators=[DataRequired(), validate_date_format], render_kw={'placeholder': 'e.g. 2025-06-01', 'aria-label': 'Date'})
    auto_email = BooleanField(default=False, render_kw={'aria-label': 'Send Email Notification'})
    record_id = SelectField(choices=[('', 'Create New Record')], validators=[Optional()], render_kw={'aria-label': 'Select Record'})
    submit = SubmitField(render_kw={'aria-label': 'Submit Expense Form'})

class EmergencyFundForm(FlaskForm):
    first_name = StringField('First Name', validators=[DataRequired()], render_kw={'placeholder': 'e.g. John', 'aria-label': 'First Name', 'data-tooltip': 'Enter your first name.'})
    email = EmailField('Email', validators=[DataRequired(), Email()], render_kw={'placeholder': 'e.g. john.doe@example.com', 'aria-label': 'Email', 'data-tooltip': 'Enter your email address.'})
    language = SelectField('Language', choices=[('English', 'English'), ('Hausa', 'Hausa')], validators=[DataRequired()], render_kw={'aria-label': 'Language', 'data-tooltip': 'Select your preferred language.'})
    monthly_expenses = FloatField('Monthly Expenses (₦)', validators=[DataRequired(), NumberRange(min=0, max=100000000)], render_kw={'placeholder': 'e.g. ₦50,000', 'aria-label': 'Monthly Expenses', 'data-tooltip': 'Enter your total monthly expenses.'})
    auto_email = BooleanField('Send Email Notification', default=False, render_kw={'aria-label': 'Send Email Notification', 'data-tooltip': 'Check to receive email notifications.'})
    record_id = SelectField('Select Record to Edit', choices=[('', 'Create New Record')], validators=[Optional()], render_kw={'aria-label': 'Select Record', 'data-tooltip': 'Select a previous record to edit or create a new one.'})
    submit = SubmitField('Calculate Emergency Fund', render_kw={'aria-label': 'Submit Emergency Fund Form'})

class BillPlannerForm(FlaskForm):
    def validate_due_date(self, field):
        try:
            parse(field.data)
        except ValueError:
            raise ValidationError('Due date must be in YYYY-MM-DD format.')

    first_name = StringField('First Name', validators=[DataRequired()], render_kw={'placeholder': 'e.g. John', 'aria-label': 'First Name', 'data-tooltip': 'Enter your first name.'})
    email = EmailField('Email', validators=[DataRequired(), Email()], render_kw={'placeholder': 'e.g. john.doe@example.com', 'aria-label': 'Email', 'data-tooltip': 'Enter your email address.'})
    language = SelectField('Language', choices=[('English', 'English'), ('Hausa', 'Hausa')], validators=[DataRequired()], render_kw={'aria-label': 'Language', 'data-tooltip': 'Select your preferred language.'})
    description = TextAreaField('Description', validators=[DataRequired()], render_kw={'placeholder': 'e.g. Electricity bill', 'aria-label': 'Description', 'data-tooltip': 'Describe the bill.'})
    amount = FloatField('Amount (₦)', validators=[DataRequired(), NumberRange(min=0, max=10000000000)], render_kw={'placeholder': 'e.g. ₦10,000', 'aria-label': 'Amount', 'data-tooltip': 'Enter the bill amount.'})
    due_date = StringField('Due Date', validators=[DataRequired(), validate_due_date], render_kw={'placeholder': 'e.g. 2025-06-01', 'aria-label': 'Due Date', 'data-tooltip': 'Enter the bill due date (YYYY-MM-DD).'})
    category = SelectField('Category', choices=[
        ('Utilities', 'Utilities'),
        ('Housing', 'Housing'),
        ('Transport', 'Transport'),
        ('Food', 'Food'),
        ('Other', 'Other')
    ], validators=[DataRequired()], render_kw={'aria-label': 'Category', 'data-tooltip': 'Select the bill category.'})
    recurrence = SelectField('Recurrence', choices=[
        ('None', 'None'),
        ('Daily', 'Daily'),
        ('Weekly', 'Weekly'),
        ('Monthly', 'Monthly'),
        ('Yearly', 'Yearly')
    ], validators=[DataRequired()], render_kw={'aria-label': 'Recurrence', 'data-tooltip': 'Select if the bill recurs.'})
    auto_email = BooleanField('Send Email Notification', default=False, render_kw={'aria-label': 'Send Email Notification', 'data-tooltip': 'Check to receive email notifications.'})
    record_id = SelectField('Select Record to Edit', choices=[('', 'Create New Record')], validators=[Optional()], render_kw={'aria-label': 'Select Record', 'data-tooltip': 'Select a previous record to edit or create a new one.'})
    submit = SubmitField('Add Bill', render_kw={'aria-label': 'Submit Bill Form'})

# Form classes mapping
form_classes = {
    'HealthScore': HealthScoreForm,
    'NetWorth': NetWorthForm,
    'Quiz': QuizForm,
    'EmergencyFund': EmergencyFundForm,
    'Budget': BudgetForm,
    'ExpenseTracker': ExpenseTrackerForm,
    'BillPlanner': BillPlannerForm
}

# Calculate quiz results
def calculate_quiz_results(answers, language='English'):
    score = sum(1 for q in answers if q == 'Yes')
    if score >= 8:
        personality = get_translation('Strategist', language)
    elif score >= 4:
        personality = get_translation('Planner', language)
    else:
        personality = get_translation('Learner', language)
    return score, personality

# Synchronous email sending
def send_email_sync(subject, recipients, html, language='English'):
    if not app.config['MAIL_ENABLED']:
        logger.warning("Email functionality is disabled.")
        return
    try:
        msg = Message(subject, sender='ficore.ai.africa@gmail.com', recipients=recipients)
        msg.html = html
        mail.send(msg)
    except Exception as e:
        logger.error(f"Error sending email: {e}")
        flash(get_translation('Failed to send email notification', language), 'warning')

# Calculate health score
def calculate_health_score(income_revenue, expenses_costs, debt_loan, debt_interest_rate):
    if not income_revenue or income_revenue <= 0:
        return 0
    score = 100
    expense_ratio = expenses_costs / income_revenue
    debt_ratio = debt_loan / income_revenue
    if expense_ratio > 1:
        score -= 40
    else:
        score -= 40 * expense_ratio
    if debt_ratio > 1:
        score -= 30
    else:
        score -= 30 * debt_ratio
    if debt_interest_rate:
        score -= min(0.5 * debt_interest_rate, 20)
    return max(0, round(score, 2))

# Get score description
def get_score_description(score, language='English'):
    if score >= 80:
        return get_translation('Strong Financial Health', language)
    elif score >= 60:
        return get_translation('Stable Finances', language)
    elif score >= 40:
        return get_translation('Financial Strain', language)
    else:
        return get_translation('Urgent Attention Needed', language)

# Assign rank
@cache.memoize(timeout=300)
def assign_rank(score):
    language = session.get('language', 'English')
    try:
        sheet = initialize_worksheet('HealthScore')
        if sheet is None:
            logger.error("Cannot assign rank: HealthScore worksheet not initialized")
            return 1, 1
        all_scores = [parse_number(row.get('score', 0)) for row in sheet.get_all_records() if row.get('score') is not None]
        all_scores.append(score)
        sorted_scores = sorted(all_scores, reverse=True)
        rank = sorted_scores.index(score) + 1
        total_users = len(all_scores)
        return rank, total_users
    except gspread.exceptions.APIError as e:
        logger.error(f"Google Sheets API error assigning rank: {e}")
        flash(get_translation('Failed to assign rank due to Google Sheets API limit', language), 'error')
        return 1, 1
    except Exception as e:
        logger.error(f"Error assigning rank: {e}")
        flash(get_translation('Failed to assign rank due to server error', language), 'error')
        return 1, 1

# Assign badges
def assign_badges(score, debt, income, language='English'):
    badges = []
    try:
        if score >= 60:
            badges.append(get_translation('Financial Stability Achieved!', language))
        if debt == 0:
            badges.append(get_translation('Debt Slayer!', language))
        if income > 0:
            badges.append(get_translation('First Health Score Completed!', language))
        if score >= 80:
            badges.append(get_translation('High Value Badge', language))
        elif score >= 60:
            badges.append(get_translation('Positive Value Badge', language))
    except Exception as e:
        logger.error(f"Error assigning badges: {e}")
    return badges

# Generate health score charts
@cache.memoize(timeout=300)
def generate_health_score_charts(income_revenue, debt_loan, health_score, average_score, language):
    translations = {
        'English': {
            'Money You Get': 'Money You Get',
            'Money You Owe': 'Money You Owe',
            'Your Score': 'Your Score',
            'Average Score': 'Average Score',
            'Financial Health': 'Financial Health'
        },
        'Hausa': {
            'Money You Get': 'Kuɗin da Kuke Samu',
            'Money You Owe': 'Kuɗin da Kuke Bin Bashi',
            'Your Score': 'Makin Ku',
            'Average Score': 'Matsakaicin Maki',
            'Financial Health': 'Lafiyar Kuɗi'
        }
    }
    fig1 = go.Figure(data=[
        go.Bar(
            x=[translations[language]['Money You Get'], translations[language]['Money You Owe']],
            y=[income_revenue, debt_loan],
            marker_color=['#2E7D32', '#D32F2F']
        )
    ])
    fig1.update_layout(
        title=translations[language]['Financial Health'],
        yaxis_title='Amount (₦)',
        showlegend=False,
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)'
    )
    chart_html = pio.to_html(fig1, include_plotlyjs=True, full_html=False)
    fig2 = go.Figure(data=[
        go.Bar(
            x=[translations[language]['Your Score'], translations[language]['Average Score']],
            y=[health_score, average_score],
            marker_color=['#0288D1', '#FFB300']
        )
    ])
    fig2.update_layout(
        title=translations[language]['Financial Health'],
        yaxis_title='Score',
        showlegend=False,
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)'
    )
    comparison_chart_html = pio.to_html(fig2, include_plotlyjs=False, full_html=False)
    return chart_html, comparison_chart_html

# Generate net worth charts
@cache.memoize(timeout=300)
def generate_net_worth_charts(assets, liabilities, net_worth, language='English'):
    try:
        labels = [get_translation('Assets', language), get_translation('Liabilities', language)]
        values = [assets, liabilities]
        pie_fig = go.Figure(data=[go.Pie(labels=labels, values=values, hole=0.3, marker=dict(colors=['#2E7D32', '#DC3545']))])
        pie_fig.update_layout(
            title=get_translation('Asset-Liability Breakdown', language),
            showlegend=True,
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font=dict(size=12),
            hovermode='closest'
        )
        chart_html = pio.to_html(pie_fig, full_html=False, include_plotlyjs=True)
        sheet = initialize_worksheet('NetWorth')
        if sheet is None:
            logger.error("Cannot generate net worth charts: NetWorth worksheet not initialized")
            return get_translation('Chart failed to load. Please try again.', language), ""
        all_net_worths = [parse_number(row.get('net_worth', 0)) for row in sheet.get_all_records() if row.get('net_worth')]
        avg_net_worth = np.mean(all_net_worths) if all_net_worths else 0
        bar_fig = go.Figure(data=[
            go.Bar(name=get_translation('Your Net Worth', language), x=['You'], y=[net_worth], marker_color='#2E7D32'),
            go.Bar(name=get_translation('Average Net Worth', language), x=['Average'], y=[avg_net_worth], marker_color='#0288D1')
        ])
        bar_fig.update_layout(
            title=get_translation('Comparison to Peers', language),
            barmode='group',
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font=dict(size=12),
            hovermode='closest'
        )
        comparison_chart_html = pio.to_html(bar_fig, full_html=False, include_plotlyjs=True)
        return chart_html, comparison_chart_html
    except gspread.exceptions.APIError as e:
        logger.error(f"Google Sheets API error generating net worth charts: {e}")
        flash(get_translation('Failed to generate charts due to Google Sheets API limit', language), 'error')
        return get_translation('Chart failed to load. Please try again.', language), ""
    except Exception as e:
        logger.error(f"Error generating net worth charts: {e}")
        flash(get_translation('Failed to generate charts due to server error', language), 'error')
        return get_translation('Chart failed to load. Please try again.', language), ""

# Generate budget charts
@cache.memoize(timeout=300)
def generate_budget_charts(monthly_income, housing_expenses, food_expenses, transport_expenses, other_expenses, savings, language='English'):
    try:
        labels = [
            get_translation('Housing', language),
            get_translation('Food', language),
            get_translation('Transport', language),
            get_translation('Other', language),
            get_translation('Savings', language)
        ]
        values = [
            housing_expenses,
            food_expenses,
            transport_expenses,
            other_expenses,
            max(savings, 0)
        ]
        pie_fig = go.Figure(data=[go.Pie(labels=labels, values=values, hole=0.3, marker=dict(colors=['#2E7D32', '#DC3545', '#0288D1', '#FFB300', '#4CAF50']))])
        pie_fig.update_layout(
            title=get_translation('Budget Breakdown', language),
            showlegend=True,
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font=dict(size=12),
            hovermode='closest'
        )
        chart_html = pio.to_html(pie_fig, full_html=False, include_plotlyjs=True)
        return chart_html
    except Exception as e:
        logger.error(f"Error generating budget charts: {e}")
        flash(get_translation('Failed to generate charts due to server error', language), 'error')
        return get_translation('Chart failed to load. Please try again.', language)

# Generate quiz charts
@cache.memoize(timeout=300)
def generate_quiz_charts(quiz_score, language='English'):
    try:
        fig = go.Figure(data=[
            go.Bar(
                x=[get_translation('Your Score', language)],
                y=[quiz_score],
                marker_color='#2E7D32'
            )
        ])
        fig.update_layout(
            title=get_translation('Quiz Score', language),
            yaxis_title='Score (out of 10)',
            showlegend=False,
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)'
        )
        chart_html = pio.to_html(fig, include_plotlyjs=True, full_html=False)
        return chart_html
    except Exception as e:
        logger.error(f"Error generating quiz charts: {e}")
        flash(get_translation('Failed to generate charts due to server error', language), 'error')
        return get_translation('Chart failed to load. Please try again.', language)

# Generate emergency fund charts
@cache.memoize(timeout=300)
def generate_emergency_fund_charts(monthly_expenses, recommended_fund, language='English'):
    try:
        translations = {
            'English': {
                'Monthly Expenses': 'Monthly Expenses',
                'Recommended Fund': 'Recommended Fund',
                'Emergency Fund': 'Emergency Fund'
            }
        }
        fig = go.Figure(data=[
            go.Bar(
                x=[translations[language]['Monthly Expenses'], translations[language]['Recommended Fund']],
                y=[monthly_expenses, recommended_fund],
                marker_color=['#D32F2F', '#2E7D32']
            )
        ])
        fig.update_layout(
            title=translations[language]['Emergency Fund'],
            yaxis_title='Amount (₦)',
            showlegend=False,
            plot_bgcolor='rgba(0,0,0,0)',
            paper_bgcolor='rgba(0,0,0,0)'
        )
        chart_html = pio.to_html(fig, include_plotlyjs=True, full_html=False)
        return chart_html
    except Exception as e:
        logger.error(f"Error generating emergency fund charts: {e}")
        flash(get_translation('Failed to generate charts due to server error', language), 'error')
        return get_translation('Chart failed to load. Please try again.', language)

# Assign net worth rank
@cache.memoize(timeout=300)
def assign_net_worth_rank(net_worth):
    language = session.get('language', 'English')
    try:
        sheet = initialize_worksheet('NetWorth')
        if sheet is None:
            logger.error("Cannot assign net worth rank: NetWorth worksheet not initialized")
            return 50.0
        all_net_worths = [parse_number(row.get('net_worth', 0)) for row in sheet.get_all_records() if row.get('net_worth')]
        all_net_worths.append(net_worth)
        rank_percentile = 100 - np.percentile(all_net_worths, np.searchsorted(sorted(all_net_worths, reverse=True), net_worth) / len(all_net_worths) * 100)
        return round(rank_percentile, 1)
    except gspread.exceptions.APIError as e:
        logger.error(f"Google Sheets API error assigning net worth rank: {e}")
        flash(get_translation('Failed to assign rank due to Google Sheets API limit', language), 'error')
        return 50.0
    except Exception as e:
        logger.error(f"Error assigning net worth rank: {e}")
        flash(get_translation('Failed to assign rank due to server error', language), 'error')
        return 50.0

# Get net worth advice
def get_net_worth_advice(net_worth, language='English'):
    if net_worth > 0:
        return get_translation('Maintain your positive net worth by continuing to manage liabilities and grow assets.', language)
    elif net_worth == 0:
        return get_translation('Your net worth is balanced. Consider increasing assets to build wealth.', language)
    else:
        return get_translation('Focus on reducing liabilities to improve your net worth.', language)

# Assign net worth badges
def assign_net_worth_badges(net_worth, language='English'):
    badges = []
    try:
        if net_worth > 0:
            badges.append(get_translation('Positive Net Worth', language))
        if net_worth >= 1000000:
            badges.append(get_translation('Wealth Builder', language))
        if net_worth <= 0:
            badges.append(get_translation('Debt Reduction Focus', language))
    except Exception as e:
        logger.error(f"Error assigning net worth badges: {e}")
    return badges

# Calculate budget rank
@cache.memoize(timeout=300)
def calculate_budget_rank(surplus_deficit):
    language = session.get('language', 'English')
    try:
        sheet = initialize_worksheet('Budget')
        if sheet is None:
            logger.error("Cannot calculate budget rank: Budget worksheet not initialized")
            return 1, 1
        all_surpluses = [parse_number(row.get('surplus_deficit', 0)) for row in sheet.get_all_records() if row.get('surplus_deficit')]
        all_surpluses.append(surplus_deficit)
        sorted_surpluses = sorted(all_surpluses, reverse=True)
        rank = sorted_surpluses.index(surplus_deficit) + 1
        total_users = len(all_surpluses)
        return rank, total_users
    except gspread.exceptions.APIError as e:
        logger.error(f"Google Sheets API error calculating budget rank: {e}")
        flash(get_translation('Failed to calculate rank due to Google Sheets API limit', language), 'error')
        return 1, 1
    except Exception as e:
        logger.error(f"Error calculating budget rank: {e}")
        flash(get_translation('Failed to calculate rank due to server error', language), 'error')
        return 1, 1

# Assign budget badges
def assign_budget_badges(surplus_deficit, language='English'):
    badges = []
    try:
        if surplus_deficit > 0:
            badges.append(get_translation('Surplus Achiever', language))
        elif surplus_deficit == 0:
            badges.append(get_translation('Balanced Budget', language))
        else:
            badges.append(get_translation('Expense Manager', language))
    except Exception as e:
        logger.error(f"Error assigning budget badges: {e}")
    return badges

# Routes
@app.route('/')
def home():
    language = session.get('language', 'English')
    return render_template('index.html', language=language, translations=translations.get(language, translations['English']))

@app.route('/set_language', methods=['POST'])
def set_language():
    language = request.form.get('language', 'English')
    session['language'] = language
    return redirect(request.referrer or url_for('home'))

@app.route('/health_score', methods=['GET', 'POST'])
def health_score_form():
    language = session.get('language', 'English')
    form = HealthScoreForm()
    user_email = session.get('user_email')
    if user_email:
        form.email.data = user_email
        form.confirm_email.data = user_email
        records = get_user_data_by_email(user_email, 'HealthScore')
        form.record_id.choices = [('', get_translation('Create New Record', language))] + [
            (record.get('id', record.get('timestamp')), f"{record.get('timestamp')} - {record.get('business_name')}")
            for record in records
        ]
    if form.validate_on_submit():
        session['language'] = form.language.data
        session['user_email'] = form.email.data
        score = calculate_health_score(
            form.monthly_income.data,
            form.monthly_expenses.data,
            form.debt_loan.data,
            form.debt_interest_rate.data or 0
        )
        sheet = initialize_worksheet('HealthScore')
        if sheet is None:
            flash(get_translation('Failed to save data due to Google Sheets error', language), 'error')
            return redirect(url_for('health_score_form'))
        all_scores = [parse_number(row.get('score', 0)) for row in sheet.get_all_records() if row.get('score')]
        average_score = sum(all_scores) / len(all_scores) if all_scores else 0
        rank, total_users = assign_rank(score)
        badges = assign_badges(score, form.debt_loan.data, form.monthly_income.data, language)
        record_id = form.record_id.data or str(uuid.uuid4())
        health_data = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'business_name': form.business_name.data,
            'monthly_income': form.monthly_income.data,
            'monthly_expenses': form.monthly_expenses.data,
            'debt_loan': form.debt_loan.data,
            'debt_interest_rate': form.debt_interest_rate.data or 0,
            'auto_email': str(form.auto_email.data),
            'phone_number': form.phone_number.data,
            'first_name': form.first_name.data,
            'last_name': form.last_name.data,
            'user_type': form.user_type.data,
            'email': form.email.data,
            'id': record_id,
            'badges': ', '.join(badges),
            'language': form.language.data,
            'score': score
        }
        update_or_append_user_data(health_data, 'HealthScore')
        store_authentication_data({
            'first_name': form.first_name.data,
            'email': form.email.data,
            'last_name': form.last_name.data,
            'phone': form.phone_number.data,
            'language': form.language.data
        })
        chart_html, comparison_chart_html = generate_health_score_charts(
            form.monthly_income.data,
            form.debt_loan.data,
            score,
            average_score,
            language
        )
        if form.auto_email.data and app.config['MAIL_ENABLED']:
            subject = get_translation('Your Financial Health Score', language)
            html = render_template(
                'email_templates/health_score_email.html',
                score=score,
                description=get_score_description(score, language),
                chart_html=chart_html,
                comparison_chart_html=comparison_chart_html,
                language=language,
                translations=translations.get(language, translations['English'])
            )
            send_email_sync(subject, [form.email.data], html, language)
        return render_template(
            'health_score_result.html',
            score=score,
            description=get_score_description(score, language),
            chart_html=chart_html,
            comparison_chart_html=comparison_chart_html,
            rank=rank,
            total_users=total_users,
            badges=badges,
            language=language,
            translations=translations.get(language, translations['English'])
        )
    return render_template('health_score.html', form=form, language=language, translations=translations.get(language, translations['English']))

@app.route('/net_worth', methods=['GET', 'POST'])
def net_worth_form():
    language = session.get('language', 'English')
    form = NetWorthForm()
    user_email = session.get('user_email')
    if user_email:
        form.email.data = user_email
        records = get_user_data_by_email(user_email, 'NetWorth')
        form.record_id.choices = [('', get_translation('Create New Record', language))] + [
            (record.get('id', record.get('timestamp')), f"{record.get('timestamp')} - Net Worth: ₦{record.get('net_worth', 0):,.2f}")
            for record in records
        ]
    if form.validate_on_submit():
        session['language'] = form.language.data
        session['user_email'] = form.email.data
        net_worth = form.assets.data - form.liabilities.data
        rank_percentile = assign_net_worth_rank(net_worth)
        advice = get_net_worth_advice(net_worth, language)
        badges = assign_net_worth_badges(net_worth, language)
        record_id = form.record_id.data or str(uuid.uuid4())
        net_worth_data = {
            'id': record_id,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'first_name': form.first_name.data,
            'email': form.email.data,
            'language': form.language.data,
            'assets': form.assets.data,
            'liabilities': form.liabilities.data,
            'net_worth': net_worth
        }
        update_or_append_user_data(net_worth_data, 'NetWorth')
        store_authentication_data({
            'first_name': form.first_name.data,
            'email': form.email.data,
            'language': form.language.data
        })
        chart_html, comparison_chart_html = generate_net_worth_charts(form.assets.data, form.liabilities.data, net_worth, language)
        return render_template(
            'net_worth_result.html',
            net_worth=net_worth,
            rank_percentile=rank_percentile,
            advice=advice,
            badges=badges,
            chart_html=chart_html,
            comparison_chart_html=comparison_chart_html,
            language=language,
            translations=translations.get(language, translations['English'])
        )
    return render_template('net_worth.html', form=form, language=language, translations=translations.get(language, translations['English']))

@app.route('/quiz', methods=['GET', 'POST'])
def quiz_form():
    language = session.get('language', 'English')
    form = QuizForm()
    user_email = session.get('user_email')
    if user_email:
        form.email.data = user_email
        records = get_user_data_by_email(user_email, 'Quiz')
        form.record_id.choices = [('', get_translation('Create New Record', language))] + [
            (record.get('timestamp'), f"{record.get('timestamp')} - Score: {record.get('quiz_score', 0)}")
            for record in records
        ]
    if form.validate_on_submit():
        session['language'] = form.language.data
        session['user_email'] = form.email.data
        answers = [form[f'q{i}'].data for i in range(1, 11)]
        quiz_score, personality = calculate_quiz_results(answers, language)
        quiz_data = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'first_name': form.first_name.data,
            'email': form.email.data,
            'language': form.language.data,
            'q1': answers[0],
            'q2': answers[1],
            'q3': answers[2],
            'q4': answers[3],
            'q5': answers[4],
            'q6': answers[5],
            'q7': answers[6],
            'q8': answers[7],
            'q9': answers[8],
            'q10': answers[9],
            'quiz_score': quiz_score,
            'personality': personality,
            'auto_email': str(form.auto_email.data)
        }
        update_or_append_user_data(quiz_data, 'Quiz')
        store_authentication_data({
            'first_name': form.first_name.data,
            'email': form.email.data,
            'language': form.language.data
        })
        chart_html = generate_quiz_charts(quiz_score, language)
        if form.auto_email.data and app.config['MAIL_ENABLED']:
            subject = get_translation('Your Financial Quiz Results', language)
            html = render_template(
                'email_templates/quiz_email.html',
                score=quiz_score,
                personality=personality,
                chart_html=chart_html,
                language=language,
                translations=translations.get(language, translations['English'])
            )
            send_email_sync(subject, [form.email.data], html, language)
        return render_template(
            'quiz_result.html',
            score=quiz_score,
            personality=personality,
            chart_html=chart_html,
            language=language,
            translations=translations.get(language, translations['English'])
        )
    return render_template('quiz.html', form=form, language=language, translations=translations.get(language, translations['English']))

@app.route('/emergency_fund', methods=['GET', 'POST'])
def emergency_fund_form():
    language = session.get('language', 'English')
    form = EmergencyFundForm()
    user_email = session.get('user_email')
    if user_email:
        form.email.data = user_email
        records = get_user_data_by_email(user_email, 'EmergencyFund')
        form.record_id.choices = [('', get_translation('Create New Record', language))] + [
            (record.get('timestamp'), f"{record.get('timestamp')} - Fund: ₦{record.get('recommended_fund', 0):,.2f}")
            for record in records
        ]
    if form.validate_on_submit():
        session['language'] = form.language.data
        session['user_email'] = form.email.data
        recommended_fund = form.monthly_expenses.data * 3
        emergency_data = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'first_name': form.first_name.data,
            'email': form.email.data,
            'language': form.language.data,
            'monthly_expenses': form.monthly_expenses.data,
            'recommended_fund': recommended_fund,
            'auto_email': str(form.auto_email.data)
        }
        update_or_append_user_data(emergency_data, 'EmergencyFund')
        store_authentication_data({
            'first_name': form.first_name.data,
            'email': form.email.data,
            'language': form.language.data
        })
        chart_html = generate_emergency_fund_charts(form.monthly_expenses.data, recommended_fund, language)
        if form.auto_email.data and app.config['MAIL_ENABLED']:
            subject = get_translation('Your Emergency Fund Recommendation', language)
            html = render_template(
                'email_templates/emergency_fund_email.html',
                recommended_fund=recommended_fund,
                chart_html=chart_html,
                language=language,
                translations=translations.get(language, translations['English'])
            )
            send_email_sync(subject, [form.email.data], html, language)
        return render_template(
            'emergency_fund_result.html',
            recommended_fund=recommended_fund,
            chart_html=chart_html,
            language=language,
            translations=translations.get(language, translations['English'])
        )
    return render_template('emergency_fund.html', form=form, language=language, translations=translations.get(language, translations['English']))

@app.route('/budget', methods=['GET', 'POST'])
def budget_form():
    language = session.get('language', 'English')
    form = BudgetForm(language=language)
    user_email = session.get('user_email')
    if user_email:
        form.email.data = user_email
        form.confirm_email.data = user_email
        records = get_user_data_by_email(user_email, 'Budget')
        form.record_id.choices = [('', get_translation('Create New Record', language))] + [
            (record.get('timestamp'), f"{record.get('timestamp')} - Surplus/Deficit: ₦{record.get('surplus_deficit', 0):,.2f}")
            for record in records
        ]
    if form.validate_on_submit():
        session['language'] = form.language.data
        session['user_email'] = form.email.data
        total_expenses = (
            form.housing_expenses.data +
            form.food_expenses.data +
            form.transport_expenses.data +
            form.other_expenses.data
        )
        savings = form.monthly_income.data * 0.2
        surplus_deficit = form.monthly_income.data - total_expenses - savings
        rank, total_users = calculate_budget_rank(surplus_deficit)
        badges = assign_budget_badges(surplus_deficit, language)
        budget_data = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'first_name': form.first_name.data,
            'email': form.email.data,
            'confirm_email': form.confirm_email.data,
            'auto_email': str(form.auto_email.data),
            'language': form.language.data,
            'monthly_income': form.monthly_income.data,
            'housing_expenses': form.housing_expenses.data,
            'food_expenses': form.food_expenses.data,
            'transport_expenses': form.transport_expenses.data,
            'other_expenses': form.other_expenses.data,
            'total_expenses': total_expenses,
            'savings': savings,
            'surplus_deficit': surplus_deficit,
            'rank': rank,
            'total_users': total_users,
            'badges': ', '.join(badges)
        }
        update_or_append_user_data(budget_data, 'Budget')
        store_authentication_data({
            'first_name': form.first_name.data,
            'email': form.email.data,
            'language': form.language.data
        })
        chart_html = generate_budget_charts(
            form.monthly_income.data,
            form.housing_expenses.data,
            form.food_expenses.data,
            form.transport_expenses.data,
            form.other_expenses.data,
            savings,
            language
        )
        if form.auto_email.data and app.config['MAIL_ENABLED']:
            subject = get_translation('Your Budget Plan', language)
            html = render_template(
                'email_templates/budget_email.html',
                total_expenses=total_expenses,
                savings=savings,
                surplus_deficit=surplus_deficit,
                chart_html=chart_html,
                rank=rank,
                total_users=total_users,
                badges=badges,
                language=language,
                translations=translations.get(language, translations['English'])
            )
            send_email_sync(subject, [form.email.data], html, language)
        return render_template(
            'budget_result.html',
            total_expenses=total_expenses,
            savings=savings,
            surplus_deficit=surplus_deficit,
            chart_html=chart_html,
            rank=rank,
            total_users=total_users,
            badges=badges,
            language=language,
            translations=translations.get(language, translations['English'])
        )
    return render_template('budget.html', form=form, language=language, translations=translations.get(language, translations['English']))

@app.route('/expense_tracker', methods=['GET', 'POST'])
def expense_tracker_form():
    language = session.get('language', 'English')
    form = ExpenseTrackerForm(language=language)
    user_email = session.get('user_email')
    if user_email:
        form.email.data = user_email
        form.confirm_email.data = user_email
        records = get_user_data_by_email(user_email, 'ExpenseTracker')
        form.record_id.choices = [('', get_translation('Create New Record', language))] + [
            (record.get('id', record.get('timestamp')), f"{record.get('date')} - {record.get('description')}: ₦{record.get('amount', 0):,.2f}")
            for record in records
        ]
    if form.validate_on_submit():
        session['language'] = form.language.data
        session['user_email'] = form.email.data
        record_id = form.record_id.data or str(uuid.uuid4())
        expense_data = {
            'id': record_id,
            'email': form.email.data,
            'amount': form.amount.data,
            'category': form.category.data,
            'date': form.date.data,
            'description': form.description.data,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'transaction_type': form.transaction_type.data,
            'running_balance': 0,
            'first_name': form.first_name.data,
            'language': form.language.data,
            'auto_email': str(form.auto_email.data)
        }
        update_or_append_user_data(expense_data, 'ExpenseTracker')
        running_balance = calculate_running_balance(form.email.data)
        store_authentication_data({
            'first_name': form.first_name.data,
            'email': form.email.data,
            'language': form.language.data
        })
        if form.auto_email.data and app.config['MAIL_ENABLED']:
            subject = get_translation('Your Expense Tracker Update', language)
            html = render_template(
                'email_templates/expense_tracker_email.html',
                description=form.description.data,
                amount=form.amount.data,
                category=form.category.data,
                date=form.date.data,
                transaction_type=form.transaction_type.data,
                running_balance=running_balance,
                language=language,
                translations=translations.get(language, translations['English'])
            )
            send_email_sync(subject, [form.email.data], html, language)
        flash(get_translation('Transaction added successfully', language), 'success')
        return redirect(url_for('expense_tracker_dashboard', email=form.email.data))
    return render_template('expense_tracker.html', form=form, language=language, translations=translations.get(language, translations['English']))

@app.route('/expense_tracker_dashboard/<email>')
def expense_tracker_dashboard(email):
    language = session.get('language', 'English')
    if email != session.get('user_email'):
        abort(403)
    expenses = parse_expense_data(email, language)
    summary = summarize_expenses(expenses, language)
    chart_html = generate_expense_charts(email, language)
    return render_template(
        'expense_tracker_dashboard.html',
        expenses=expenses,
        summary=summary,
        chart_html=chart_html,
        language=language,
        translations=translations.get(language, translations['English'])
    )

@app.route('/bill_planner', methods=['GET', 'POST'])
def bill_planner_form():
    language = session.get('language', 'English')
    form = BillPlannerForm()
    user_email = session.get('user_email')
    if user_email:
        form.email.data = user_email
        records = get_user_data_by_email(user_email, 'BillPlanner')
        form.record_id.choices = [('', get_translation('Create New Record', language))] + [
            (record.get('timestamp'), f"{record.get('due_date')} - {record.get('description')}: ₦{record.get('amount', 0):,.2f}")
            for record in records
        ]
    if form.validate_on_submit():
        session['language'] = form.language.data
        session['user_email'] = form.email.data
        bill_data = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'first_name': form.first_name.data,
            'email': form.email.data,
            'language': form.language.data,
            'description': form.description.data,
            'amount': form.amount.data,
            'due_date': form.due_date.data,
            'category': form.category.data,
            'recurrence': form.recurrence.data,
            'status': 'Pending',
            'auto_email': str(form.auto_email.data)
        }
        update_or_append_user_data(bill_data, 'BillPlanner')
        store_authentication_data({
            'first_name': form.first_name.data,
            'email': form.email.data,
            'language': form.language.data
        })
        if form.auto_email.data and app.config['MAIL_ENABLED']:
            subject = get_translation('Your Bill Planner Update', language)
            html = render_template(
                'email_templates/bill_planner_email.html',
                description=form.description.data,
                amount=form.amount.data,
                due_date=form.due_date.data,
                category=form.category.data,
                recurrence=form.recurrence.data,
                language=language,
                translations=translations.get(language, translations['English'])
            )
            send_email_sync(subject, [form.email.data], html, language)
        flash(get_translation('Bill added successfully', language), 'success')
        return redirect(url_for('bill_dashboard', email=form.email.data))
    return render_template('bill_planner.html', form=form, language=language, translations=translations.get(language, translations['English']))

@app.route('/bill_dashboard/<email>')
def bill_dashboard(email):
    language = session.get('language', 'English')
    if email != session.get('user_email'):
        abort(403)
    bills = parse_bill_data(email, language)
    today = datetime.now().strftime('%Y-%m-%d')
    one_month_later = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
    schedule = generate_bill_schedule(bills, today, one_month_later, language)
    return render_template(
        'bill_dashboard.html',
        bills=bills,
        schedule=schedule,
        language=language,
        translations=translations.get(language, translations['English'])
    )

@app.route('/logout')
def logout():
    session.clear()
    flash(get_translation('You have been logged out', session.get('language', 'English')), 'success')
    return redirect(url_for('home'))

# Error handlers
@app.errorhandler(403)
def forbidden(e):
    language = session.get('language', 'English')
    return render_template('error.html', error_code=403, error_message=get_translation('Forbidden: You do not have access to this page', language), language=language, translations=translations.get(language, translations['English'])), 403

@app.errorhandler(404)
def not_found(e):
    language = session.get('language', 'English')
    return render_template('error.html', error_code=404, error_message=get_translation('Page not found', language), language=language, translations=translations.get(language, translations['English'])), 404

@app.errorhandler(500)
def internal_error(e):
    language = session.get('language', 'English')
    return render_template('error.html', error_code=500, error_message=get_translation('Internal server error', language), language=language, translations=translations.get(language, translations['English'])), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
