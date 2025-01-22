from flask import Flask, render_template, request, redirect, url_for, flash, abort, jsonify, send_from_directory, Response
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
from bson import ObjectId
from flask_mail import Mail, Message
from werkzeug.utils import secure_filename
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import time
import logging
import base64
import requests
import csv
from io import StringIO
from jinja2 import Environment, select_autoescape

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key-here')

# Configure logging
logging.basicConfig(level=logging.INFO)

# MongoDB setup
client = MongoClient(os.getenv('MONGODB_URI', 'mongodb://localhost:27017'))
db = client['residencehub']
users_collection = db['users']
apartments_collection = db['apartments']
tenant_profiles_collection = db['tenant_profiles']
lease_collection = db['leases']
maintenance_requests_collection = db['maintenance_requests']
announcements_collection = db['announcements']
reminders_collection = db['reminders']
documents_collection = db['documents']
payments_collection = db['payments']  # New collection for rent payments

# Scheduler setup
scheduler = BackgroundScheduler()
scheduler.start()

# Upload folders configuration
UPLOAD_FOLDER = 'static/uploads/apartments'
DOCUMENTS_FOLDER = 'static/uploads/documents'
PROFILE_PICTURES_FOLDER = 'static/uploads/profiles'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
ALLOWED_DOCUMENT_EXTENSIONS = {'pdf', 'doc', 'docx', 'txt'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['DOCUMENTS_FOLDER'] = DOCUMENTS_FOLDER
app.config['PROFILE_PICTURES_FOLDER'] = PROFILE_PICTURES_FOLDER

# Ensure upload directories exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DOCUMENTS_FOLDER, exist_ok=True)
os.makedirs(PROFILE_PICTURES_FOLDER, exist_ok=True)

# Configure Flask-Mail
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'True').lower() == 'true'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER')
mail = Mail(app)

# Configure Jinja2 for email templates
jinja_env = Environment(
    autoescape=select_autoescape(['html', 'xml'])
)

def send_email_with_template(template_name, recipient_email, subject, **kwargs):
    """Send an email using a template."""
    try:
        # Add common template variables
        kwargs.update({
            'current_year': datetime.utcnow().year,
            'recipient_email': recipient_email
        })
        
        # Render the email template
        html = render_template(f'emails/{template_name}.html', **kwargs)
        
        # Create and send the email
        msg = Message(
            subject=subject,
            recipients=[recipient_email],
            html=html
        )
        mail.send(msg)
        logging.info(f"Email sent successfully to {recipient_email}")
        return True
    except Exception as e:
        logging.error(f"Error sending email: {str(e)}")
        return False

def send_payment_confirmation(payment_id):
    """Send payment confirmation email."""
    try:
        payment = payments_collection.find_one({'_id': ObjectId(payment_id)})
        if not payment:
            return False
        
        tenant = users_collection.find_one({'_id': payment['tenant_id']})
        if not tenant:
            return False
        
        lease = lease_collection.find_one({'tenant_id': payment['tenant_id']})
        if not lease:
            return False
        
        return send_email_with_template(
            'payment_confirmation',
            tenant['email'],
            'Payment Confirmation',
            tenant_name=tenant['name'],
            amount_paid=payment['amount_paid'],
            transaction_date=payment['payment_date'].strftime('%B %d, %Y at %I:%M %p'),
            transaction_id=payment.get('transaction_id', 'N/A'),
            payment_method=payment['payment_method'].title(),
            property_name='ResidenceHub',  # Replace with actual property name
            unit_number=lease.get('unit_number', 'N/A'),
            receipt_url=url_for('view_payment_detail', payment_id=str(payment['_id']), _external=True)
        )
    except Exception as e:
        logging.error(f"Error sending payment confirmation: {str(e)}")
        return False

def send_payment_reminder(tenant_id, amount_due, due_date):
    """Send payment reminder email."""
    try:
        tenant = users_collection.find_one({'_id': ObjectId(tenant_id)})
        if not tenant:
            return False
        
        lease = lease_collection.find_one({'tenant_id': ObjectId(tenant_id)})
        if not lease:
            return False
        
        days_until_due = (due_date - datetime.utcnow()).days
        
        return send_email_with_template(
            'payment_reminder',
            tenant['email'],
            'Rent Payment Reminder',
            tenant_name=tenant['name'],
            amount_due=amount_due,
            days_until_due=days_until_due,
            due_date=due_date.strftime('%B %d, %Y'),
            property_name='ResidenceHub',  # Replace with actual property name
            unit_number=lease.get('unit_number', 'N/A'),
            payment_url=url_for('view_payments', _external=True)
        )
    except Exception as e:
        logging.error(f"Error sending payment reminder: {str(e)}")
        return False

def send_announcement_email(announcement_id, tenant_email):
    """Send announcement email to a tenant."""
    try:
        announcement = announcements_collection.find_one({'_id': ObjectId(announcement_id)})
        if not announcement:
            return False
        
        tenant = users_collection.find_one({'email': tenant_email})
        if not tenant:
            return False
        
        posted_by = users_collection.find_one({'_id': announcement['created_by']})
        
        return send_email_with_template(
            'announcement',
            tenant_email,
            f"New Announcement: {announcement['title']}",
            tenant_name=tenant['name'],
            announcement_title=announcement['title'],
            announcement_content=announcement['content'],
            announcement_priority=announcement.get('priority', 'normal'),
            announcement_images=announcement.get('images', []),
            posted_by=posted_by['name'] if posted_by else 'Management',
            posted_date=announcement['created_at'].strftime('%B %d, %Y at %I:%M %p'),
            announcement_url=url_for('view_announcements', _external=True)
        )
    except Exception as e:
        logging.error(f"Error sending announcement email: {str(e)}")
        return False

def send_maintenance_update(request_id):
    """Send maintenance request update email."""
    try:
        maintenance_request = maintenance_requests_collection.find_one({'_id': ObjectId(request_id)})
        if not maintenance_request:
            return False
        
        tenant = users_collection.find_one({'_id': maintenance_request['tenant_id']})
        if not tenant:
            return False
        
        return send_email_with_template(
            'maintenance_update',
            tenant['email'],
            'Maintenance Request Update',
            tenant_name=tenant['name'],
            request_type=maintenance_request['type'],
            request_status=maintenance_request['status'],
            submitted_date=maintenance_request['created_at'].strftime('%B %d, %Y'),
            last_updated=maintenance_request['updated_at'].strftime('%B %d, %Y at %I:%M %p'),
            technician_notes=maintenance_request.get('technician_notes'),
            request_url=url_for('maintenance_request_detail', request_id=str(maintenance_request['_id']), _external=True)
        )
    except Exception as e:
        logging.error(f"Error sending maintenance update: {str(e)}")
        return False

# Login manager setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def allowed_document(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_DOCUMENT_EXTENSIONS

def send_reminder_emails():
    current_date = datetime.utcnow()
    
    # Get all active tenants
    tenants = list(users_collection.find({'role': 'tenant'}))
    for tenant in tenants:
        tenant_id = tenant['_id']
        tenant_obj = User(tenant)
        tenant_profile = tenant_profiles_collection.find_one({'user_id': tenant_id})
        lease = lease_collection.find_one({'tenant_id': tenant_id})
        
        if not tenant_profile or not lease:
            continue
        
        # Monthly reminder based on join date
        join_date = tenant_profile.get('created_at')
        if join_date:
            # Calculate months since join date
            months = (current_date.year - join_date.year) * 12 + (current_date.month - join_date.month)
            # If it's been at least one month and today matches the join day
            if months >= 1 and current_date.day == join_date.day:
                reminder_data = {
                    'tenant_id': str(tenant_id),
                    'type': 'monthly_check',
                    'message': f'Monthly check-in for tenant {tenant["name"]}',
                    'due_date': current_date + timedelta(days=7),
                    'created_at': current_date
                }
                reminders_collection.insert_one(reminder_data)
                
                if app.config.get('MAIL_USERNAME') and app.config.get('MAIL_PASSWORD'):
                    try:
                        msg = Message(
                            'Monthly Tenant Check-in',
                            sender=app.config['MAIL_USERNAME'],
                            recipients=[tenant['email']]
                        )
                        msg.body = f'Hello {tenant["name"]},\n\nThis is your monthly check-in reminder. Please review your account and update any necessary information.\n\nBest regards,\nResidenceHub Team'
                        mail.send(msg)
                    except Exception as e:
                        print(f"Error sending monthly reminder email: {str(e)}")
        
        # Rent due reminder (5 days before)
        if lease.get('payment_status') == 'pending':
            rent_due_date = lease.get('next_payment_date')
            if rent_due_date and (rent_due_date - current_date).days == 5:
                reminder_data = {
                    'tenant_id': str(tenant_id),
                    'type': 'rent_due',
                    'message': f'Rent due for tenant {tenant["name"]}',
                    'due_date': rent_due_date,
                    'created_at': current_date
                }
                reminders_collection.insert_one(reminder_data)
                
                if app.config.get('MAIL_USERNAME') and app.config.get('MAIL_PASSWORD'):
                    try:
                        msg = Message(
                            'Rent Due Reminder',
                            sender=app.config['MAIL_USERNAME'],
                            recipients=[tenant['email']]
                        )
                        msg.body = f'Hello {tenant["name"]},\n\nThis is a reminder that your rent payment is due in 5 days.\n\nBest regards,\nResidenceHub Team'
                        mail.send(msg)
                    except Exception as e:
                        print(f"Error sending rent reminder email: {str(e)}")
        
        # Lease renewal reminder (30 days before)
        lease_end = lease.get('end_date')
        if lease_end and (lease_end - current_date).days == 30:
            reminder_data = {
                'tenant_id': str(tenant_id),
                'type': 'lease_renewal',
                'message': f'Lease renewal for tenant {tenant["name"]}',
                'due_date': lease_end,
                'created_at': current_date
            }
            reminders_collection.insert_one(reminder_data)
            
            if app.config.get('MAIL_USERNAME') and app.config.get('MAIL_PASSWORD'):
                try:
                    msg = Message(
                        'Lease Renewal Reminder',
                        sender=app.config['MAIL_USERNAME'],
                        recipients=[tenant['email']]
                    )
                    msg.body = f'Hello {tenant["name"]},\n\nYour lease is due for renewal in 30 days. Please contact management to discuss renewal options.\n\nBest regards,\nResidenceHub Team'
                    mail.send(msg)
                except Exception as e:
                    print(f"Error sending lease renewal email: {str(e)}")

def send_monthly_reminders():
    current_date = datetime.utcnow()
    
    # Get all active tenants
    active_tenants = users_collection.find({'role': 'tenant', 'status': {'$ne': 'suspended'}})
    
    for tenant in active_tenants:
        # Get tenant's join date
        tenant_profile = tenant_profiles_collection.find_one({'user_id': tenant['_id']})
        if tenant_profile and tenant_profile.get('created_at'):
            join_date = tenant_profile['created_at']
            # Calculate months since joining
            months_diff = (current_date.year - join_date.year) * 12 + (current_date.month - join_date.month)
            
            # If it's the monthly anniversary of join date
            if current_date.day == join_date.day:
                # Get tenant's lease
                lease = lease_collection.find_one({'tenant_id': tenant['_id']})
                if lease and app.config['MAIL_USERNAME'] and app.config['MAIL_PASSWORD']:
                    try:
                        message = Message(
                            subject='Monthly Rent Reminder',
                            sender=app.config['MAIL_USERNAME'],
                            recipients=[tenant['email']]
                        )
                        message.body = f"""
                        Dear {tenant['name']},
                        
                        This is your monthly rent reminder. Please ensure your payment is made on time.
                        
                        Amount Due: ${lease['rent_amount']}
                        Due Date: {(current_date + timedelta(days=5)).strftime('%B %d, %Y')}
                        
                        Best regards,
                        ResidenceHub Management
                        """
                        mail.send(message)
                        
                        # Record the reminder
                        reminders_collection.insert_one({
                            'tenant_id': tenant['_id'],
                            'type': 'monthly_rent',
                            'sent_at': current_date,
                            'due_date': current_date + timedelta(days=5)
                        })
                    except Exception as e:
                        print(f"Error sending monthly reminder email: {str(e)}")

# Schedule daily reminder check
scheduler.add_job(
    send_reminder_emails,
    CronTrigger(hour=9, minute=0),
    id='send_reminders',
    replace_existing=True
)

# Update scheduler to include monthly reminders
scheduler.add_job(
    send_monthly_reminders,
    CronTrigger(hour=9),  # Run at 9 AM every day
    id='send_monthly_reminders',
    replace_existing=True
)

class User(UserMixin):
    def __init__(self, user_data):
        self.id = str(user_data['_id'])
        self.name = user_data.get('name', '')
        self.email = user_data.get('email', '')
        self.role = user_data.get('role', '')
        self.apartment_id = user_data.get('apartment_id', None)
        self.status = user_data.get('status', 'active')
        self.profile_picture = user_data.get('profile_picture', None)

    def is_manager(self):
        return self.role == 'manager'

    def is_tenant(self):
        return self.role == 'tenant'

    def get_apartment(self):
        if not self.apartment_id:
            return None
        return apartments_collection.find_one({'_id': ObjectId(self.apartment_id)})

    def get_profile(self):
        return profiles_collection.find_one({'user_id': ObjectId(self.id)})

    def get_lease(self):
        if not self.apartment_id:
            return None
        return leases_collection.find_one({
            'tenant_id': ObjectId(self.id),
            'apartment_id': ObjectId(self.apartment_id)
        })

    @property
    def is_active(self):
        return self.status == 'active'

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def get_profile_picture_url(self):
        if not self.profile_picture:
            return url_for('static', filename='img/default-avatar.png')
        return url_for('static', filename=f'uploads/profile_pictures/{self.profile_picture}')

def manager_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_manager():
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

def tenant_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_tenant():
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

@login_manager.user_loader
def load_user(user_id):
    user_data = users_collection.find_one({'_id': ObjectId(user_id)})
    return User(user_data) if user_data else None

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        user_data = users_collection.find_one({'email': email})
        if user_data and check_password_hash(user_data['password'], password):
            user = User(user_data)
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('dashboard'))
        flash('Invalid email or password')
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
        
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        name = request.form.get('name')
        role = request.form.get('role', 'tenant')
        
        if users_collection.find_one({'email': email}):
            flash('Email already registered')
            return redirect(url_for('register'))
        
        if role not in ['tenant', 'manager']:
            flash('Invalid role selected')
            return redirect(url_for('register'))
        
        hashed_password = generate_password_hash(password)
        user_data = {
            'email': email,
            'password': hashed_password,
            'name': name,
            'role': role,
            'apartment_id': None,  # Will be assigned later
            'created_at': datetime.utcnow()
        }
        
        users_collection.insert_one(user_data)
        flash('Registration successful')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.is_manager():
        # Get all apartments and tenants for managers
        apartments = list(apartments_collection.find())
        tenants = list(users_collection.find({'role': 'tenant'}))
        return render_template('dashboard.html', apartments=apartments, tenants=tenants, datetime=datetime)
    else:
        # Get tenant's apartment and maintenance requests
        apartment = current_user.get_apartment()
        return render_template('dashboard.html', apartment=apartment, datetime=datetime)

@app.route('/manage/apartments')
@login_required
@manager_required
def manage_apartments():
    # Get apartments and convert ObjectId to string for JSON serialization
    apartments = list(apartments_collection.find())
    for apartment in apartments:
        apartment['_id'] = str(apartment['_id'])
        if apartment.get('tenant_id'):
            apartment['tenant_id'] = str(apartment['tenant_id'])
    return render_template('manage_apartments.html', apartments=apartments)

@app.route('/manage/tenants')
@login_required
@manager_required
def manage_tenants():
    tenant_docs = list(users_collection.find({'role': 'tenant'}))
    tenants = []
    for tenant_doc in tenant_docs:
        tenant_doc['_id'] = str(tenant_doc['_id'])  # Convert _id to string
        if tenant_doc.get('apartment_id'):
            tenant_doc['apartment_id'] = str(tenant_doc['apartment_id'])  # Convert apartment_id to string
        tenants.append(User(tenant_doc))
    apartments = list(apartments_collection.find())
    for apartment in apartments:
        apartment['_id'] = str(apartment['_id'])  # Convert apartment _id to string
    return render_template('manage_tenants.html', tenants=tenants, apartments=apartments)

@app.route('/tenant/maintenance')
@login_required
@tenant_required
def maintenance_requests():
    return render_template('maintenance_requests.html')

@app.route('/tenant/profile', methods=['GET', 'POST'])
@login_required
def tenant_profile():
    profile = tenant_profiles_collection.find_one({'user_id': ObjectId(current_user.id)})
    if request.method == 'POST':
        if current_user.is_tenant():
            # Update tenant's own profile
            profile_data = {
                'phone': request.form.get('phone'),
                'emergency_contact': request.form.get('emergency_contact'),
                'emergency_phone': request.form.get('emergency_phone'),
                'updated_at': datetime.utcnow()
            }
            if profile:
                tenant_profiles_collection.update_one(
                    {'user_id': ObjectId(current_user.id)},
                    {'$set': profile_data}
                )
            else:
                profile_data['user_id'] = ObjectId(current_user.id)
                profile_data['created_at'] = datetime.utcnow()
                tenant_profiles_collection.insert_one(profile_data)
            flash('Profile updated successfully')
            return redirect(url_for('tenant_profile'))
    return render_template('tenant_profile.html', profile=profile)

@app.route('/manage/tenant/<tenant_id>/edit', methods=['GET', 'POST'])
@login_required
@manager_required
def manage_tenant_profile(tenant_id):
    tenant = users_collection.find_one({'_id': ObjectId(tenant_id)})
    if not tenant:
        flash('Tenant not found')
        return redirect(url_for('manage_tenants'))
    
    profile = tenant_profiles_collection.find_one({'user_id': ObjectId(tenant_id)})
    lease = lease_collection.find_one({'tenant_id': ObjectId(tenant_id)})
    
    if request.method == 'POST':
        # Update tenant profile and lease information
        profile_data = {
            'phone': request.form.get('phone'),
            'emergency_contact': request.form.get('emergency_contact'),
            'emergency_phone': request.form.get('emergency_phone'),
            'notes': request.form.get('notes'),
            'updated_at': datetime.utcnow()
        }
        
        if profile:
            tenant_profiles_collection.update_one(
                {'user_id': ObjectId(tenant_id)},
                {'$set': profile_data}
            )
        else:
            profile_data['user_id'] = ObjectId(tenant_id)
            profile_data['created_at'] = datetime.utcnow()
            tenant_profiles_collection.insert_one(profile_data)
        
        if request.form.get('apartment_id'):
            lease_data = {
                'tenant_id': ObjectId(tenant_id),
                'apartment_id': ObjectId(request.form.get('apartment_id')),
                'start_date': datetime.strptime(request.form.get('lease_start'), '%Y-%m-%d'),
                'end_date': datetime.strptime(request.form.get('lease_end'), '%Y-%m-%d'),
                'rent_amount': float(request.form.get('rent_amount')),
                'payment_status': request.form.get('payment_status'),
                'updated_at': datetime.utcnow()
            }
            
            if lease:
                lease_collection.update_one(
                    {'tenant_id': ObjectId(tenant_id)},
                    {'$set': lease_data}
                )
            else:
                lease_data['created_at'] = datetime.utcnow()
                lease_collection.insert_one(lease_data)
            
            # Update apartment assignment
            old_apartment = apartments_collection.find_one({'tenant_id': ObjectId(tenant_id)})
            if old_apartment and str(old_apartment['_id']) != request.form.get('apartment_id'):
                apartments_collection.update_one(
                    {'_id': old_apartment['_id']},
                    {'$set': {'tenant_id': None}}
                )
            
            apartments_collection.update_one(
                {'_id': ObjectId(request.form.get('apartment_id'))},
                {'$set': {'tenant_id': ObjectId(tenant_id)}}
            )
        
        flash('Tenant profile updated successfully')
        return redirect(url_for('manage_tenants'))
    
    apartments = list(apartments_collection.find())
    return render_template('manage_tenant_profile.html', 
                         tenant=tenant, 
                         profile=profile, 
                         lease=lease, 
                         apartments=apartments)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.errorhandler(403)
def forbidden(e):
    flash('You do not have permission to access this resource')
    return redirect(url_for('dashboard'))

@app.route('/maintenance/request', methods=['GET', 'POST'])
@login_required
@tenant_required
def submit_maintenance_request():
    if request.method == 'POST':
        request_data = {
            'tenant_id': ObjectId(current_user.id),
            'apartment_id': current_user.apartment_id,
            'category': request.form.get('category'),
            'priority': request.form.get('priority'),
            'description': request.form.get('description'),
            'status': 'pending',
            'created_at': datetime.utcnow(),
            'updated_at': datetime.utcnow()
        }
        maintenance_requests_collection.insert_one(request_data)
        flash('Maintenance request submitted successfully')
        return redirect(url_for('view_maintenance_requests'))
    return render_template('submit_maintenance_request.html')

@app.route('/maintenance/requests')
@login_required
def view_maintenance_requests():
    if current_user.is_manager():
        # Managers see all requests
        requests = list(maintenance_requests_collection.find().sort('created_at', -1))
        # Get tenant and apartment details for each request
        for req in requests:
            tenant = users_collection.find_one({'_id': req['tenant_id']})
            apartment = apartments_collection.find_one({'_id': req['apartment_id']})
            req['tenant_name'] = tenant['name'] if tenant else 'Unknown'
            req['apartment_number'] = apartment['unit_number'] if apartment else 'Unknown'
    else:
        # Tenants see only their requests
        requests = list(maintenance_requests_collection.find(
            {'tenant_id': ObjectId(current_user.id)}
        ).sort('created_at', -1))
    
    return render_template('maintenance_requests.html', requests=requests)

@app.route('/maintenance/request/<request_id>', methods=['GET', 'POST'])
@login_required
def maintenance_request_detail(request_id):
    maint_request = maintenance_requests_collection.find_one({'_id': ObjectId(request_id)})
    if not maint_request:
        flash('Maintenance request not found')
        return redirect(url_for('view_maintenance_requests'))
    
    # Check authorization
    if not current_user.is_manager() and maint_request['tenant_id'] != ObjectId(current_user.id):
        abort(403)
    
    if request.method == 'POST' and current_user.is_manager():
        update_data = {
            'status': request.form.get('status'),
            'assigned_to': request.form.get('assigned_to'),
            'manager_notes': request.form.get('manager_notes'),
            'updated_at': datetime.utcnow()
        }
        maintenance_requests_collection.update_one(
            {'_id': ObjectId(request_id)},
            {'$set': update_data}
        )
        flash('Maintenance request updated successfully')
        return redirect(url_for('maintenance_request_detail', request_id=request_id))
    
    # Get related information
    tenant = users_collection.find_one({'_id': maint_request['tenant_id']})
    apartment = apartments_collection.find_one({'_id': maint_request['apartment_id']})
    maintenance_staff = list(users_collection.find({'role': 'maintenance'}))
    
    return render_template('maintenance_request_detail.html',
                         request=maint_request,
                         tenant=tenant,
                         apartment=apartment,
                         maintenance_staff=maintenance_staff)

@app.route('/maintenance/request/<request_id>/update-status', methods=['POST'])
@login_required
@manager_required
def update_maintenance_status(request_id):
    status = request.form.get('status')
    if status not in ['pending', 'in_progress', 'completed', 'cancelled']:
        flash('Invalid status', 'error')
        return redirect(url_for('maintenance_request_detail', request_id=request_id))
    
    maintenance_requests_collection.update_one(
        {'_id': ObjectId(request_id)},
        {
            '$set': {
                'status': status,
                'updated_at': datetime.utcnow()
            }
        }
    )
    flash('Status updated successfully')
    return redirect(url_for('maintenance_request_detail', request_id=request_id))

@app.route('/api/dashboard/stats')
@login_required
@manager_required
def dashboard_stats():
    # Get maintenance request stats
    active_requests = maintenance_requests_collection.count_documents({
        'status': {'$in': ['pending', 'in_progress']}
    })
    urgent_issues = maintenance_requests_collection.count_documents({
        'priority': 'emergency',
        'status': {'$ne': 'completed'}
    })
    pending_requests = maintenance_requests_collection.count_documents({'status': 'pending'})
    in_progress_requests = maintenance_requests_collection.count_documents({'status': 'in_progress'})
    completed_requests = maintenance_requests_collection.count_documents({'status': 'completed'})

    # Get occupancy stats
    total_units = apartments_collection.count_documents({})
    occupied_units = apartments_collection.count_documents({'tenant_id': {'$ne': None}})

    return jsonify({
        'active_requests': active_requests,
        'urgent_issues': urgent_issues,
        'pending_requests': pending_requests,
        'in_progress_requests': in_progress_requests,
        'completed_requests': completed_requests,
        'total_units': total_units,
        'occupied_units': occupied_units
    })

@app.route('/api/dashboard/activity')
@login_required
@manager_required
def dashboard_activity():
    # Get recent activities (maintenance requests, tenant changes, etc.)
    pipeline = [
        {'$sort': {'created_at': -1}},
        {'$limit': 10}
    ]
    
    activities = []
    
    # Get recent maintenance requests
    maintenance_requests = list(maintenance_requests_collection.aggregate(pipeline))
    for req in maintenance_requests:
        tenant = users_collection.find_one({'_id': req['tenant_id']})
        activities.append({
            'type': 'maintenance',
            'description': f"New maintenance request from {tenant['name']} - {req['category']}",
            'time': req['created_at'].strftime('%Y-%m-%d %H:%M'),
            'icon': 'fa-tools',
            'iconBg': 'bg-yellow-500'
        })
    
    # Get recent tenant changes
    tenant_changes = list(tenant_profiles_collection.aggregate(pipeline))
    for change in tenant_changes:
        tenant = users_collection.find_one({'_id': change['user_id']})
        activities.append({
            'type': 'tenant',
            'description': f"Profile updated for {tenant['name']}",
            'time': change['updated_at'].strftime('%Y-%m-%d %H:%M'),
            'icon': 'fa-user',
            'iconBg': 'bg-blue-500'
        })
    
    # Sort all activities by time
    activities.sort(key=lambda x: x['time'], reverse=True)
    activities = activities[:10]  # Keep only the 10 most recent activities

    return jsonify({'activities': activities})

@app.route('/api/tenant/requests')
@login_required
@tenant_required
def tenant_requests():
    # Get tenant's maintenance requests
    requests = list(maintenance_requests_collection.find(
        {'tenant_id': ObjectId(current_user.id)}
    ).sort('created_at', -1))
    
    # Format the requests for JSON response
    formatted_requests = []
    for req in requests:
        formatted_requests.append({
            '_id': str(req['_id']),
            'category': req['category'],
            'status': req['status'],
            'priority': req['priority'],
            'created_at': req['created_at'].strftime('%Y-%m-%d %H:%M')
        })
    
    return jsonify({'requests': formatted_requests})

@app.route('/manage/announcements', methods=['GET', 'POST'])
@login_required
@manager_required
def manage_announcements():
    if request.method == 'POST':
        announcement_data = {
            'title': request.form.get('title'),
            'content': request.form.get('content'),
            'priority': request.form.get('priority', 'normal'),
            'created_at': datetime.utcnow(),
            'created_by': ObjectId(current_user.id)
        }
        
        result = announcements_collection.insert_one(announcement_data)
        announcement_id = str(result.inserted_id)
        
        # Send email notifications to all tenants
        tenants = users_collection.find({'role': 'tenant'})
        for tenant in tenants:
            send_announcement_email(announcement_id, tenant['email'])
        
        flash('Announcement posted successfully', 'success')
        return redirect(url_for('manage_announcements'))
    
    announcements = list(announcements_collection.find().sort('created_at', -1))
    return render_template('manage_announcements.html', announcements=announcements)

@app.route('/api/announcements')
@login_required
def get_announcements():
    announcements = list(announcements_collection.find().sort('created_at', -1))
    formatted_announcements = []
    for announcement in announcements:
        manager = users_collection.find_one({'_id': announcement['created_by']})
        formatted_announcements.append({
            '_id': str(announcement['_id']),
            'title': announcement['title'],
            'content': announcement['content'],
            'priority': announcement.get('priority', 'normal'),
            'created_at': announcement['created_at'].strftime('%Y-%m-%d %H:%M'),
            'created_by': manager['name'] if manager else 'Unknown'
        })
    return jsonify({'announcements': formatted_announcements})

@app.route('/manage/tenant/add', methods=['POST'])
@login_required
@manager_required
def add_tenant():
    # Create user account for tenant
    user_data = {
        'email': request.form.get('email'),
        'password': generate_password_hash('changeme'),  # Default password that tenant should change
        'name': request.form.get('name'),
        'role': 'tenant',
        'created_at': datetime.utcnow()
    }
    user_result = users_collection.insert_one(user_data)
    tenant_id = user_result.inserted_id

    # Create tenant profile
    profile_data = {
        'user_id': tenant_id,
        'phone': request.form.get('phone'),
        'created_at': datetime.utcnow()
    }
    tenant_profiles_collection.insert_one(profile_data)

    # Create lease
    lease_data = {
        'tenant_id': tenant_id,
        'apartment_id': ObjectId(request.form.get('apartment_id')),
        'start_date': datetime.strptime(request.form.get('lease_start'), '%Y-%m-%d'),
        'end_date': datetime.strptime(request.form.get('lease_end'), '%Y-%m-%d'),
        'rent_amount': float(request.form.get('rent_amount')),
        'payment_status': 'pending',
        'created_at': datetime.utcnow()
    }
    lease_collection.insert_one(lease_data)

    # Update apartment with tenant
    apartments_collection.update_one(
        {'_id': ObjectId(request.form.get('apartment_id'))},
        {'$set': {'tenant_id': tenant_id}}
    )

    flash('Tenant added successfully')
    return redirect(url_for('manage_tenants'))

@app.route('/manage/apartment/add', methods=['POST'])
@login_required
@manager_required
def add_apartment():
    apartment_data = {
        'unit_number': request.form.get('unit_number'),
        'floor': int(request.form.get('floor')),
        'type': request.form.get('type'),
        'size': int(request.form.get('size')),
        'rent': float(request.form.get('rent')),
        'description': request.form.get('description'),
        'tenant_id': None,
        'created_at': datetime.utcnow(),
        'images': []
    }
    
    # Handle image uploads
    if 'images' in request.files:
        files = request.files.getlist('images')
        for file in files:
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                # Add timestamp to filename to make it unique
                filename = f"{datetime.utcnow().timestamp()}_{filename}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                apartment_data['images'].append(filename)
    
    apartments_collection.insert_one(apartment_data)
    flash('Apartment added successfully')
    return redirect(url_for('manage_apartments'))

@app.route('/manage/apartment/<apartment_id>', methods=['GET', 'POST'])
@login_required
@manager_required
def edit_apartment(apartment_id):
    apartment = apartments_collection.find_one({'_id': ObjectId(apartment_id)})
    if not apartment:
        flash('Apartment not found')
        return redirect(url_for('manage_apartments'))
    
    if request.method == 'POST':
        update_data = {
            'unit_number': request.form.get('unit_number'),
            'floor': int(request.form.get('floor')),
            'type': request.form.get('type'),
            'size': int(request.form.get('size')),
            'rent': float(request.form.get('rent')),
            'description': request.form.get('description'),
            'updated_at': datetime.utcnow()
        }
        apartments_collection.update_one(
            {'_id': ObjectId(apartment_id)},
            {'$set': update_data}
        )
        flash('Apartment updated successfully')
        return redirect(url_for('manage_apartments'))
    
    return render_template('edit_apartment.html', apartment=apartment)

@app.template_global()
def get_tenant(tenant_id):
    if not tenant_id:
        return None
    tenant_data = users_collection.find_one({'_id': ObjectId(tenant_id)})
    return User(tenant_data) if tenant_data else None

@app.route('/manage/tenant/<tenant_id>/suspend', methods=['POST'])
@login_required
@manager_required
def suspend_tenant(tenant_id):
    users_collection.update_one(
        {'_id': ObjectId(tenant_id)},
        {'$set': {'status': 'suspended', 'suspended_at': datetime.utcnow()}}
    )
    flash('Tenant has been suspended')
    return redirect(url_for('manage_tenant_profile', tenant_id=tenant_id))

@app.route('/manage/tenant/<tenant_id>/activate', methods=['POST'])
@login_required
@manager_required
def activate_tenant(tenant_id):
    users_collection.update_one(
        {'_id': ObjectId(tenant_id)},
        {'$set': {'status': 'active'}, '$unset': {'suspended_at': ''}}
    )
    flash('Tenant has been activated')
    return redirect(url_for('manage_tenant_profile', tenant_id=tenant_id))

@app.route('/manage/tenant/<tenant_id>/delete', methods=['POST'])
@login_required
@manager_required
def delete_tenant(tenant_id):
    # Get tenant's apartment
    tenant = users_collection.find_one({'_id': ObjectId(tenant_id)})
    if tenant:
        # Remove tenant from apartment
        apartments_collection.update_many(
            {'tenant_id': ObjectId(tenant_id)},
            {'$set': {'tenant_id': None}}
        )
        # Delete tenant's profile
        tenant_profiles_collection.delete_one({'user_id': ObjectId(tenant_id)})
        # Delete tenant's lease
        lease_collection.delete_one({'tenant_id': ObjectId(tenant_id)})
        # Delete tenant's maintenance requests
        maintenance_requests_collection.delete_many({'tenant_id': ObjectId(tenant_id)})
        # Finally delete the tenant
        users_collection.delete_one({'_id': ObjectId(tenant_id)})
        flash('Tenant has been deleted')
    else:
        flash('Tenant not found')
    return redirect(url_for('manage_tenants'))

@app.route('/manage/apartment/<apartment_id>/delete', methods=['POST'])
@login_required
@manager_required
def delete_apartment(apartment_id):
    apartment = apartments_collection.find_one({'_id': ObjectId(apartment_id)})
    if apartment:
        # Delete apartment images
        if apartment.get('images'):
            for image in apartment['images']:
                try:
                    os.remove(os.path.join(app.config['UPLOAD_FOLDER'], image))
                except:
                    pass
        
        # Update any tenants that were in this apartment
        if apartment.get('tenant_id'):
            users_collection.update_one(
                {'_id': apartment['tenant_id']},
                {'$set': {'apartment_id': None}}
            )
            # Update related lease
            lease_collection.update_one(
                {'apartment_id': ObjectId(apartment_id)},
                {'$set': {'status': 'terminated', 'terminated_at': datetime.utcnow()}}
            )
        
        # Delete maintenance requests for this apartment
        maintenance_requests_collection.delete_many({'apartment_id': ObjectId(apartment_id)})
        # Finally delete the apartment
        apartments_collection.delete_one({'_id': ObjectId(apartment_id)})
        flash('Apartment has been deleted')
    else:
        flash('Apartment not found')
    return redirect(url_for('manage_apartments'))

@app.route('/manage/apartment/<apartment_id>/remove-image/<image_name>', methods=['POST'])
@login_required
@manager_required
def remove_apartment_image(apartment_id, image_name):
    try:
        os.remove(os.path.join(app.config['UPLOAD_FOLDER'], image_name))
    except:
        pass
    
    apartments_collection.update_one(
        {'_id': ObjectId(apartment_id)},
        {'$pull': {'images': image_name}}
    )
    flash('Image removed successfully')
    return redirect(url_for('edit_apartment', apartment_id=apartment_id))

@app.route('/manage/apartment/<apartment_id>/add-images', methods=['POST'])
@login_required
@manager_required
def add_apartment_images(apartment_id):
    if 'images' not in request.files:
        return jsonify({'error': 'No images provided'}), 400
    
    new_images = []
    files = request.files.getlist('images')
    for file in files:
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filename = f"{datetime.utcnow().timestamp()}_{filename}"
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            new_images.append(filename)
    
    if new_images:
        apartments_collection.update_one(
            {'_id': ObjectId(apartment_id)},
            {'$push': {'images': {'$each': new_images}}}
        )
    
    return jsonify({'success': True, 'images': new_images})

@app.route('/manage/apartment/<apartment_id>/update-status', methods=['POST'])
@login_required
@manager_required
def update_apartment_status(apartment_id):
    status = request.form.get('status')
    if status not in ['available', 'maintenance', 'reserved']:
        flash('Invalid status', 'error')
        return redirect(url_for('edit_apartment', apartment_id=apartment_id))
    
    apartments_collection.update_one(
        {'_id': ObjectId(apartment_id)},
        {'$set': {
            'status': status,
            'updated_at': datetime.utcnow()
        }}
    )
    flash('Apartment status updated successfully')
    return redirect(url_for('edit_apartment', apartment_id=apartment_id))

@app.route('/announcement/<announcement_id>/edit', methods=['GET', 'POST'])
@login_required
@manager_required
def edit_announcement(announcement_id):
    announcement = announcements_collection.find_one({'_id': ObjectId(announcement_id)})
    if not announcement:
        flash('Announcement not found')
        return redirect(url_for('manage_announcements'))
    
    if request.method == 'POST':
        update_data = {
            'title': request.form.get('title'),
            'content': request.form.get('content'),
            'priority': request.form.get('priority', 'normal'),
            'updated_at': datetime.utcnow(),
            'updated_by': ObjectId(current_user.id)
        }
        
        # Handle image uploads
        if 'images' in request.files:
            images = []
            files = request.files.getlist('images')
            for file in files:
                if file and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    filename = f"{datetime.utcnow().timestamp()}_{filename}"
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'announcements', filename))
                    images.append(filename)
            if images:
                update_data['images'] = images
        
        announcements_collection.update_one(
            {'_id': ObjectId(announcement_id)},
            {'$set': update_data}
        )
        flash('Announcement updated successfully')
        return redirect(url_for('manage_announcements'))
    
    return render_template('edit_announcement.html', announcement=announcement)

@app.route('/maintenance/request/<request_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_maintenance_request(request_id):
    maint_request = maintenance_requests_collection.find_one({'_id': ObjectId(request_id)})
    if not maint_request:
        flash('Maintenance request not found')
        return redirect(url_for('view_maintenance_requests'))
    
    # Check authorization
    if not current_user.is_manager() and maint_request['tenant_id'] != ObjectId(current_user.id):
        abort(403)
    
    if request.method == 'POST':
        update_data = {
            'category': request.form.get('category'),
            'priority': request.form.get('priority'),
            'description': request.form.get('description'),
            'updated_at': datetime.utcnow()
        }
        
        # Only managers can update these fields
        if current_user.is_manager():
            update_data.update({
                'status': request.form.get('status'),
                'assigned_to': request.form.get('assigned_to'),
                'manager_notes': request.form.get('manager_notes')
            })
        
        # Handle image uploads
        if 'images' in request.files:
            images = []
            files = request.files.getlist('images')
            for file in files:
                if file and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    filename = f"{datetime.utcnow().timestamp()}_{filename}"
                    file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'maintenance', filename))
                    images.append(filename)
            if images:
                update_data['images'] = images
        
        maintenance_requests_collection.update_one(
            {'_id': ObjectId(request_id)},
            {'$set': update_data}
        )
        flash('Maintenance request updated successfully')
        return redirect(url_for('maintenance_request_detail', request_id=request_id))
    
    # Get related information
    tenant = users_collection.find_one({'_id': maint_request['tenant_id']})
    apartment = apartments_collection.find_one({'_id': maint_request['apartment_id']})
    maintenance_staff = list(users_collection.find({'role': 'maintenance'}))
    
    return render_template('edit_maintenance_request.html',
                         request=maint_request,
                         tenant=tenant,
                         apartment=apartment,
                         maintenance_staff=maintenance_staff)

@app.route('/maintenance/request/<request_id>/delete', methods=['POST'])
@login_required
@manager_required
def delete_maintenance_request(request_id):
    maintenance_requests_collection.delete_one({'_id': ObjectId(request_id)})
    flash('Maintenance request deleted successfully')
    return redirect(url_for('view_maintenance_requests'))

@app.route('/manage/tenant/<tenant_id>/update-status', methods=['POST'])
@login_required
@manager_required
def update_tenant_status(tenant_id):
    status = request.form.get('status')
    if status not in ['active', 'inactive', 'suspended']:
        flash('Invalid status', 'error')
        return redirect(url_for('manage_tenant_profile', tenant_id=tenant_id))
    
    users_collection.update_one(
        {'_id': ObjectId(tenant_id)},
        {'$set': {
            'status': status,
            'updated_at': datetime.utcnow()
        }}
    )
    flash('Tenant status updated successfully')
    return redirect(url_for('manage_tenant_profile', tenant_id=tenant_id))

@app.route('/announcement/<announcement_id>/update-priority', methods=['POST'])
@login_required
@manager_required
def update_announcement_priority(announcement_id):
    priority = request.form.get('priority')
    if priority not in ['normal', 'urgent']:
        flash('Invalid priority', 'error')
        return redirect(url_for('edit_announcement', announcement_id=announcement_id))
    
    announcements_collection.update_one(
        {'_id': ObjectId(announcement_id)},
        {'$set': {
            'priority': priority,
            'updated_at': datetime.utcnow()
        }}
    )
    flash('Announcement priority updated successfully')
    return redirect(url_for('edit_announcement', announcement_id=announcement_id))

@app.route('/announcement/<announcement_id>/delete', methods=['POST'])
@login_required
@manager_required
def delete_announcement(announcement_id):
    # Delete any associated images
    announcement = announcements_collection.find_one({'_id': ObjectId(announcement_id)})
    if announcement and announcement.get('images'):
        for image in announcement['images']:
            try:
                os.remove(os.path.join(app.config['UPLOAD_FOLDER'], 'announcements', image))
            except:
                pass
    
    announcements_collection.delete_one({'_id': ObjectId(announcement_id)})
    flash('Announcement deleted successfully')
    return redirect(url_for('manage_announcements'))

@app.route('/api/reminders')
@login_required
def get_reminders():
    current_date = datetime.utcnow()
    
    if current_user.is_manager():
        # Get all upcoming reminders for managers
        reminders = list(reminders_collection.find({
            'due_date': {'$gt': current_date}
        }).sort('due_date', 1).limit(5))
        
        # Format reminders with tenant information
        formatted_reminders = []
        for reminder in reminders:
            tenant = users_collection.find_one({'_id': reminder['tenant_id']})
            if tenant:
                formatted_reminders.append({
                    'tenant_name': tenant['name'],
                    'type': reminder['type'],
                    'due_date': reminder['due_date'].strftime('%Y-%m-%d'),
                    'sent_at': reminder['sent_at'].strftime('%Y-%m-%d %H:%M')
                })
    else:
        # Get tenant's own reminders
        reminders = list(reminders_collection.find({
            'tenant_id': ObjectId(current_user.id),
            'due_date': {'$gt': current_date}
        }).sort('due_date', 1).limit(5))
        
        # Format reminders
        formatted_reminders = [{
            'type': reminder['type'],
            'due_date': reminder['due_date'].strftime('%Y-%m-%d'),
            'sent_at': reminder['sent_at'].strftime('%Y-%m-%d %H:%M')
        } for reminder in reminders]
    
    return jsonify({'reminders': formatted_reminders})

@app.route('/manage/documents')
@login_required
@manager_required
def manage_documents():
    tenant_docs = list(users_collection.find({'role': 'tenant'}))
    tenants = [User(tenant_doc) for tenant_doc in tenant_docs]
    return render_template('manage_documents.html', tenants=tenants)

@app.route('/manage/documents/upload', methods=['POST'])
@login_required
@manager_required
def upload_document():
    if 'document' not in request.files:
        flash('No document file uploaded')
        return redirect(request.referrer)
    
    file = request.files['document']
    if file.filename == '':
        flash('No selected file')
        return redirect(request.referrer)
    
    if not allowed_document(file.filename):
        flash('Invalid file type')
        return redirect(request.referrer)
    
    filename = secure_filename(file.filename)
    timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S_')
    unique_filename = timestamp + filename
    file.save(os.path.join(app.config['DOCUMENTS_FOLDER'], unique_filename))
    
    document_data = {
        'filename': unique_filename,
        'original_name': filename,
        'uploaded_by': current_user.id,
        'tenant_id': request.form.get('tenant_id'),
        'type': request.form.get('type', 'general'),
        'description': request.form.get('description', ''),
        'created_at': datetime.utcnow()
    }
    documents_collection.insert_one(document_data)
    
    flash('Document uploaded successfully')
    return redirect(request.referrer)

@app.route('/documents/<document_id>')
@login_required
def get_document(document_id):
    document = documents_collection.find_one({'_id': ObjectId(document_id)})
    if not document:
        flash('Document not found')
        return redirect(url_for('dashboard'))
    
    if not current_user.is_manager():
        if str(document.get('tenant_id')) != current_user.id:
            flash('Access denied')
            return redirect(url_for('dashboard'))
    
    try:
        return send_from_directory(
            app.config['DOCUMENTS_FOLDER'],
            document['filename'],
            as_attachment=True,
            download_name=document['original_name']
        )
    except Exception as e:
        flash('Error downloading document')
        return redirect(url_for('dashboard'))

@app.route('/api/documents')
@login_required
def get_documents():
    if current_user.is_manager():
        documents = list(documents_collection.find().sort('created_at', -1))
    else:
        documents = list(documents_collection.find({
            '$or': [
                {'tenant_id': current_user.id},
                {'type': 'general'}
            ]
        }).sort('created_at', -1))
    
    for doc in documents:
        doc['_id'] = str(doc['_id'])
        doc['uploaded_by'] = str(doc['uploaded_by'])
        if doc.get('tenant_id'):
            doc['tenant_id'] = str(doc['tenant_id'])
        doc['created_at'] = doc['created_at'].strftime('%Y-%m-%d %H:%M:%S')
    
    return jsonify(documents)

@app.route('/documents')
@login_required
def view_documents():
    return render_template('documents.html')

@app.route('/upload/profile-picture', methods=['POST'])
@login_required
def upload_profile_picture():
    if 'profile_picture' not in request.files:
        return jsonify({'success': False, 'message': 'No file uploaded'})
    
    file = request.files['profile_picture']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'No selected file'})
        
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S_')
        filename = timestamp + filename
        file.save(os.path.join(app.config['PROFILE_PICTURES_FOLDER'], filename))
        
        # Delete old profile picture if it exists
        old_picture = users_collection.find_one({'_id': ObjectId(current_user.id)}).get('profile_picture')
        if old_picture:
            try:
                os.remove(os.path.join(app.config['PROFILE_PICTURES_FOLDER'], old_picture))
            except:
                pass
        
        # Update user's profile picture in database
        users_collection.update_one(
            {'_id': ObjectId(current_user.id)},
            {'$set': {'profile_picture': filename}}
        )
        return jsonify({
            'success': True, 
            'message': 'Profile picture updated successfully',
            'profile_picture_url': url_for('static', filename=f'uploads/profile_pictures/{filename}')
        })
    
    return jsonify({'success': False, 'message': 'Invalid file type'})

@app.route('/api/user-data')
@login_required
def get_user_data():
    user_data = {
        'id': current_user.id,
        'name': current_user.name,
        'email': current_user.email,
        'role': current_user.role,
        'profile_picture_url': current_user.get_profile_picture_url()
    }
    
    if current_user.is_tenant():
        profile = current_user.get_profile()
        lease = current_user.get_lease()
        apartment = current_user.get_apartment()
        
        if profile:
            user_data.update({
                'phone': profile.get('phone'),
                'emergency_contact': profile.get('emergency_contact'),
                'emergency_phone': profile.get('emergency_phone')
            })
        
        if lease:
            user_data.update({
                'lease_start': lease.get('start_date').strftime('%Y-%m-%d'),
                'lease_end': lease.get('end_date').strftime('%Y-%m-%d'),
                'rent_amount': lease.get('rent_amount'),
                'payment_status': lease.get('payment_status')
            })
        
        if apartment:
            user_data.update({
                'unit_number': apartment.get('unit_number'),
                'apartment_type': apartment.get('type')
            })
    
    return jsonify(user_data)

@app.route('/api/tenant/<tenant_id>/data')
@login_required
@manager_required
def get_tenant_data(tenant_id):
    tenant_doc = users_collection.find_one({'_id': ObjectId(tenant_id)})
    if not tenant_doc:
        return jsonify({'error': 'Tenant not found'}), 404
    
    tenant = User(tenant_doc)
    profile = tenant.get_profile()
    lease = tenant.get_lease()
    apartment = tenant.get_apartment()
    
    tenant_data = {
        'id': tenant.id,
        'name': tenant.name,
        'email': tenant.email,
        'profile_picture_url': tenant.get_profile_picture_url(),
        'status': tenant_doc.get('status', 'active')
    }
    
    if profile:
        tenant_data.update({
            'phone': profile.get('phone'),
            'emergency_contact': profile.get('emergency_contact'),
            'emergency_phone': profile.get('emergency_phone'),
            'notes': profile.get('notes')
        })
    
    if lease:
        tenant_data.update({
            'lease_start': lease.get('start_date').strftime('%Y-%m-%d'),
            'lease_end': lease.get('end_date').strftime('%Y-%m-%d'),
            'rent_amount': lease.get('rent_amount'),
            'payment_status': lease.get('payment_status')
        })
    
    if apartment:
        tenant_data.update({
            'unit_number': apartment.get('unit_number'),
            'apartment_type': apartment.get('type')
        })
    
    return jsonify(tenant_data)

@app.route('/api/profile', methods=['GET'])
@login_required
def get_profile_data():
    profile = tenant_profiles_collection.find_one({'user_id': ObjectId(current_user.id)})
    apartment = current_user.get_apartment()
    lease = current_user.get_lease()
    
    response = {
        'name': current_user.name,
        'email': current_user.email,
        'phone': profile.get('phone') if profile else None,
        'emergency_contact': profile.get('emergency_contact') if profile else None,
        'emergency_phone': profile.get('emergency_phone') if profile else None,
        'profile_picture': current_user.profile_picture
    }
    
    if apartment:
        response['apartment'] = {
            'unit_number': apartment.get('unit_number'),
            'floor': apartment.get('floor')
        }
        
    if lease:
        response['lease'] = {
            'start_date': lease.get('start_date'),
            'end_date': lease.get('end_date'),
            'rent_amount': lease.get('rent_amount')
        }
    
    return jsonify(response)

@app.route('/tenant/profile', methods=['POST'])
@login_required
@tenant_required
def update_profile():
    profile_data = {
        'user_id': ObjectId(current_user.id),
        'phone': request.form.get('phone'),
        'emergency_contact': request.form.get('emergency_contact'),
        'emergency_phone': request.form.get('emergency_phone'),
        'updated_at': datetime.utcnow()
    }
    
    # Handle profile picture upload
    if 'profile_picture' in request.files:
        file = request.files['profile_picture']
        if file and allowed_file(file.filename, {'png', 'jpg', 'jpeg'}):
            # Generate unique filename
            filename = secure_filename(f"{current_user.id}_{int(time.time())}{os.path.splitext(file.filename)[1]}")
            file_path = os.path.join(PROFILE_PICTURES_FOLDER, filename)
            
            # Save the file
            file.save(file_path)
            
            # Update user's profile picture in database
            users_collection.update_one(
                {'_id': ObjectId(current_user.id)},
                {'$set': {'profile_picture': filename}}
            )
    
    # Update or create profile
    tenant_profiles_collection.update_one(
        {'user_id': ObjectId(current_user.id)},
        {'$set': profile_data},
        upsert=True
    )
    
    flash('Profile updated successfully')
    return redirect(url_for('tenant_profile'))

# Payment Routes
@app.route('/api/payments/history')
@login_required
def get_payment_history():
    """Get payment history for the current user or all tenants for managers."""
    if current_user.is_manager():
        # Get all payments for managers
        payments = list(payments_collection.find().sort('payment_date', -1))
        # Add tenant details to each payment
        for payment in payments:
            tenant = users_collection.find_one({'_id': payment['tenant_id']})
            if tenant:
                payment['tenant_name'] = tenant['name']
                payment['tenant_email'] = tenant['email']
    else:
        # Get tenant's own payments
        payments = list(payments_collection.find({
            'tenant_id': ObjectId(current_user.id)
        }).sort('payment_date', -1))
    
    # Format payments for JSON response
    formatted_payments = []
    for payment in payments:
        formatted_payment = {
            '_id': str(payment['_id']),
            'amount_paid': payment['amount_paid'],
            'payment_date': payment['payment_date'].strftime('%Y-%m-%d %H:%M:%S'),
            'payment_status': payment['payment_status'],
            'payment_method': payment['payment_method'],
            'transaction_id': payment.get('transaction_id', ''),
            'error_message': payment.get('error_message', '')
        }
        if current_user.is_manager():
            formatted_payment.update({
                'tenant_name': payment.get('tenant_name', 'Unknown'),
                'tenant_email': payment.get('tenant_email', '')
            })
        formatted_payments.append(formatted_payment)
    
    return jsonify({'payments': formatted_payments})

@app.route('/api/payments/stats')
@login_required
def get_payment_stats():
    """Get payment statistics."""
    if current_user.is_manager():
        # Get payment statistics for managers
        total_payments = payments_collection.count_documents({'payment_status': 'completed'})
        pending_payments = payments_collection.count_documents({'payment_status': 'pending'})
        failed_payments = payments_collection.count_documents({'payment_status': 'failed'})
        
        # Calculate total amount collected
        completed_payments = list(payments_collection.find({'payment_status': 'completed'}))
        total_amount = sum(payment['amount_paid'] for payment in completed_payments)
        
        return jsonify({
            'total_payments': total_payments,
            'pending_payments': pending_payments,
            'failed_payments': failed_payments,
            'total_amount': total_amount
        })
    else:
        # Get payment statistics for tenant
        total_payments = payments_collection.count_documents({
            'tenant_id': ObjectId(current_user.id),
            'payment_status': 'completed'
        })
        pending_payments = payments_collection.count_documents({
            'tenant_id': ObjectId(current_user.id),
            'payment_status': 'pending'
        })
        failed_payments = payments_collection.count_documents({
            'tenant_id': ObjectId(current_user.id),
            'payment_status': 'failed'
        })
        
        # Calculate total amount paid
        completed_payments = list(payments_collection.find({
            'tenant_id': ObjectId(current_user.id),
            'payment_status': 'completed'
        }))
        total_amount = sum(payment['amount_paid'] for payment in completed_payments)
        
        return jsonify({
            'total_payments': total_payments,
            'pending_payments': pending_payments,
            'failed_payments': failed_payments,
            'total_amount': total_amount
        })

@app.route('/api/payments/detail/<payment_id>')
@login_required
def get_payment_detail(payment_id):
    """Get detailed payment information."""
    if not current_user.is_manager():
        return jsonify({'error': 'Unauthorized'}), 403
    
    try:
        payment = payments_collection.find_one({'_id': ObjectId(payment_id)})
        if not payment:
            return jsonify({'error': 'Payment not found'}), 404
        
        # Get tenant details
        tenant = users_collection.find_one({'_id': payment['tenant_id']})
        tenant_details = {
            'name': tenant['name'] if tenant else 'Unknown',
            'email': tenant['email'] if tenant else '',
            'phone': tenant['phone'] if tenant else '',
            'id_number': tenant.get('id_number', ''),
            'emergency_contact': tenant.get('emergency_contact', {}),
            'join_date': tenant.get('join_date', '').strftime('%Y-%m-%d') if tenant.get('join_date') else ''
        }
        
        # Get apartment and lease details
        lease = lease_collection.find_one({'tenant_id': payment['tenant_id']})
        apartment_details = {}
        if lease:
            apartment = apartments_collection.find_one({'_id': lease['apartment_id']})
            if apartment:
                apartment_details = {
                    'apartment_number': apartment['apartment_number'],
                    'floor': apartment['floor'],
                    'monthly_rent': apartment['monthly_rent'],
                    'lease_start': lease['start_date'].strftime('%Y-%m-%d'),
                    'lease_end': lease['end_date'].strftime('%Y-%m-%d'),
                    'payment_status': lease['payment_status'],
                    'deposit_amount': lease.get('deposit_amount', 0),
                    'rent_due_day': lease.get('rent_due_day', 5)
                }
        
        # Get tenant's payment history
        payment_history = list(payments_collection.find({
            'tenant_id': payment['tenant_id']
        }).sort('payment_date', -1))
        
        formatted_history = []
        for hist_payment in payment_history:
            formatted_history.append({
                '_id': str(hist_payment['_id']),
                'amount_paid': hist_payment['amount_paid'],
                'payment_date': hist_payment['payment_date'].strftime('%Y-%m-%d %H:%M:%S'),
                'payment_status': hist_payment['payment_status'],
                'payment_method': hist_payment['payment_method'],
                'transaction_id': hist_payment.get('transaction_id', ''),
                'created_at': hist_payment['created_at'].strftime('%Y-%m-%d %H:%M:%S'),
                'completed_at': hist_payment.get('completed_at', '').strftime('%Y-%m-%d %H:%M:%S') if hist_payment.get('completed_at') else None,
                'error_message': hist_payment.get('error_message', '')
            })
        
        # Calculate payment statistics
        total_paid = sum(p['amount_paid'] for p in payment_history if p['payment_status'] == 'completed')
        pending_amount = sum(p['amount_paid'] for p in payment_history if p['payment_status'] == 'pending')
        failed_amount = sum(p['amount_paid'] for p in payment_history if p['payment_status'] == 'failed')
        
        # Format current payment details
        payment_details = {
            '_id': str(payment['_id']),
            'amount_paid': payment['amount_paid'],
            'payment_date': payment['payment_date'].strftime('%Y-%m-%d %H:%M:%S'),
            'payment_status': payment['payment_status'],
            'payment_method': payment['payment_method'],
            'transaction_id': payment.get('transaction_id', ''),
            'created_at': payment['created_at'].strftime('%Y-%m-%d %H:%M:%S'),
            'completed_at': payment.get('completed_at', '').strftime('%Y-%m-%d %H:%M:%S') if payment.get('completed_at') else None,
            'mpesa_response': payment.get('mpesa_response', {}),
            'mpesa_callback': payment.get('mpesa_callback', {}),
            'error_message': payment.get('error_message', '')
        }
        
        return jsonify({
            'payment': payment_details,
            'tenant': tenant_details,
            'apartment': apartment_details,
            'payment_history': formatted_history,
            'payment_stats': {
                'total_paid': total_paid,
                'pending_amount': pending_amount,
                'failed_amount': failed_amount,
                'total_transactions': len(payment_history)
            }
        })
        
    except Exception as e:
        logging.error(f"Error getting payment detail: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/payments/download/<payment_id>')
@login_required
def download_payment_detail(payment_id):
    """Download payment details as CSV."""
    if not current_user.is_manager():
        return jsonify({'error': 'Unauthorized'}), 403
    
    try:
        payment = payments_collection.find_one({'_id': ObjectId(payment_id)})
        if not payment:
            return jsonify({'error': 'Payment not found'}), 404
        
        # Get tenant details
        tenant = users_collection.find_one({'_id': payment['tenant_id']})
        
        # Get apartment and lease details
        lease = lease_collection.find_one({'tenant_id': payment['tenant_id']})
        apartment = None
        if lease:
            apartment = apartments_collection.find_one({'_id': lease['apartment_id']})
        
        # Get payment history
        payment_history = list(payments_collection.find({
            'tenant_id': payment['tenant_id']
        }).sort('payment_date', -1))
        
        # Create CSV content
        csv_content = []
        csv_content.append(['Payment Details Report'])
        csv_content.append([])
        
        # Tenant Information
        csv_content.append(['Tenant Information'])
        csv_content.append(['Name', tenant['name'] if tenant else 'Unknown'])
        csv_content.append(['Email', tenant['email'] if tenant else ''])
        csv_content.append(['Phone', tenant['phone'] if tenant else ''])
        csv_content.append(['ID Number', tenant.get('id_number', '')])
        csv_content.append([])
        
        # Apartment Information
        if apartment and lease:
            csv_content.append(['Apartment Information'])
            csv_content.append(['Apartment Number', apartment['apartment_number']])
            csv_content.append(['Floor', apartment['floor']])
            csv_content.append(['Monthly Rent', apartment['monthly_rent']])
            csv_content.append(['Lease Start', lease['start_date'].strftime('%Y-%m-%d')])
            csv_content.append(['Lease End', lease['end_date'].strftime('%Y-%m-%d')])
            csv_content.append([])
        
        # Payment History
        csv_content.append(['Payment History'])
        csv_content.append(['Date', 'Amount', 'Status', 'Method', 'Transaction ID'])
        for hist_payment in payment_history:
            csv_content.append([
                hist_payment['payment_date'].strftime('%Y-%m-%d %H:%M:%S'),
                hist_payment['amount_paid'],
                hist_payment['payment_status'],
                hist_payment['payment_method'],
                hist_payment.get('transaction_id', '')
            ])
        
        # Create response
        output = StringIO()
        writer = csv.writer(output)
        writer.writerows(csv_content)
        
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={
                'Content-Disposition': f'attachment; filename=payment_details_{payment_id}.csv'
            }
        )
        
    except Exception as e:
        logging.error(f"Error downloading payment detail: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/manage/payments')
@login_required
@manager_required
def manage_payments():
    """Render the payments management page for managers."""
    return render_template('manage_payments.html')

@app.route('/payments')
@login_required
def view_payments():
    """Render the payments page for tenants."""
    return render_template('payments.html')

@app.route('/manage/payment/<payment_id>')
@login_required
def view_payment_detail(payment_id):
    """Render payment detail page for managers."""
    if not current_user.is_manager():
        flash('Unauthorized access', 'error')
        return redirect(url_for('dashboard'))
    return render_template('payment_detail.html', payment_id=payment_id)

def get_access_token():
    """Generate OAuth access token."""
    consumer_key = "frmypHgIJYc7mQuUu5NBvnYc0kF1StP3"
    consumer_secret = "UAeJAJLNUkV5MLpL"
    url = "https://api.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
    
    # Create auth string and encode to base64
    auth_string = f"{consumer_key}:{consumer_secret}"
    auth_bytes = auth_string.encode('ascii')
    auth_b64 = base64.b64encode(auth_bytes).decode('ascii')
    
    headers = {
        "Authorization": f"Basic {auth_b64}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.get(url, headers=headers)
        logging.info(f"Access token response status: {response.status_code}")
        logging.info(f"Access token response: {response.text}")
        response.raise_for_status()
        return response.json()['access_token']
    except requests.exceptions.RequestException as e:
        logging.error(f"Error getting access token: {str(e)}")
        raise

@app.route('/stkpush', methods=['POST'])
@login_required
def stk_push():
    """STK push route."""
    # Log received request
    logging.info("Received request body: %s", request.json)
    
    # Validate required fields
    if not request.json or 'phone' not in request.json or 'amount' not in request.json:
        return jsonify({
            "error": "Missing required fields. Please provide both 'phone' and 'amount'"
        }), 400

    try:
        phone_number = str(request.json['phone']).strip()
        amount = request.json['amount']

        # Format phone number
        phone_number = phone_number.replace('+', '').replace(' ', '')
        phone_number = ''.join(filter(str.isdigit, phone_number))
        if not phone_number.startswith('254'):
            phone_number = '254' + phone_number.lstrip('0')

        # Validate amount is a positive number
        try:
            amount = int(float(amount))
            if amount <= 0:
                return jsonify({"error": "Amount must be greater than 0"}), 400
        except (ValueError, TypeError):
            return jsonify({"error": "Invalid amount provided"}), 400

        access_token = get_access_token()
        url = "https://api.safaricom.co.ke/mpesa/stkpush/v1/processrequest"
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        
        password_str = f"4121151{'68cb945afece7b529b4a0901b2d8b1bb3bd9daa19bfdb48c69bec8dde962a932'}{timestamp}"
        password = base64.b64encode(password_str.encode()).decode('utf-8')

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        payload = {
            "BusinessShortCode": "4121151",
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": amount,
            "PartyA": phone_number,
            "PartyB": "4121151",
            "PhoneNumber": phone_number,
            "CallBackURL": "https://github.com/BotCoder254",
            "AccountReference": "KIOTA",
            "TransactionDesc": "Mpesa Daraja API stk push test"
        }

        logging.info(f"Sending STK push request to: {url}")
        logging.info(f"Headers: {headers}")
        logging.info(f"Payload: {payload}")
        
        response = requests.post(url, json=payload, headers=headers)
        logging.info(f"STK push response status: {response.status_code}")
        logging.info(f"STK push response: {response.text}")
        
        if response.status_code != 200:
            return jsonify({"error": f"M-Pesa API returned status code {response.status_code}", "details": response.text}), 500
            
        response.raise_for_status()
        response_data = response.json()

        # Create payment record in database
        if response_data.get('ResponseCode') == '0':
            payment_data = {
                'tenant_id': ObjectId(current_user.id),
                'amount_paid': amount,
                'payment_date': datetime.utcnow(),
                'payment_status': 'pending',
                'payment_method': 'mpesa',
                'transaction_id': response_data.get('CheckoutRequestID'),
                'created_at': datetime.utcnow(),
                'mpesa_response': response_data
            }
            payments_collection.insert_one(payment_data)

        return jsonify(response_data)

    except requests.exceptions.RequestException as e:
        logging.error(f"Request failed: {str(e)}")
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        logging.error(f"Unexpected error: {str(e)}")
        return jsonify({"error": "An unexpected error occurred"}), 500

@app.route('/query', methods=['POST'])
def query():
    """Query transaction status."""
    if not request.json or 'queryCode' not in request.json:
        return jsonify({"error": "Missing queryCode"}), 400

    query_code = request.json['queryCode']

    try:
        access_token = get_access_token()
        url = "https://api.safaricom.co.ke/mpesa/stkpushquery/v1/query"
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        
        password_str = f"4121151{'68cb945afece7b529b4a0901b2d8b1bb3bd9daa19bfdb48c69bec8dde962a932'}{timestamp}"
        password = base64.b64encode(password_str.encode()).decode('utf-8')

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        payload = {
            "BusinessShortCode": "4121151",
            "Password": password,
            "Timestamp": timestamp,
            "CheckoutRequestID": query_code
        }

        logging.info(f"Sending query request to: {url}")
        logging.info(f"Headers: {headers}")
        logging.info(f"Payload: {payload}")
        
        response = requests.post(url, json=payload, headers=headers)
        logging.info(f"Query response status: {response.status_code}")
        logging.info(f"Query response: {response.text}")
        response.raise_for_status()
        result = response.json()

        # Update payment status in database if successful
        if result.get('ResultCode') == '0':
            payment = payments_collection.find_one_and_update(
                {'transaction_id': query_code},
                {'$set': {
                    'payment_status': 'completed',
                    'completed_at': datetime.utcnow(),
                    'mpesa_query_response': result
                }},
                return_document=True
            )
            if payment:
                # Send payment confirmation email
                send_payment_confirmation(str(payment['_id']))
        elif result.get('ResultCode') == '1037':  # Timeout
            payment = payments_collection.find_one_and_update(
                {'transaction_id': query_code},
                {'$set': {
                    'payment_status': 'failed',
                    'error_message': 'Payment request timed out',
                    'mpesa_query_response': result
                }},
                return_document=True
            )

        return jsonify(result)

    except requests.exceptions.RequestException as e:
        logging.error(f"Request failed: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/maintenance/update_status/<request_id>', methods=['POST'])
@login_required
@manager_required
def update_maintenance_status_api(request_id):
    """Update maintenance request status via API."""
    if not request.json or 'status' not in request.json:
        return jsonify({"error": "Missing status"}), 400
    
    new_status = request.json['status']
    technician_notes = request.json.get('notes', '')
    
    # Update the maintenance request
    maintenance_request = maintenance_requests_collection.find_one_and_update(
        {'_id': ObjectId(request_id)},
        {'$set': {
            'status': new_status,
            'technician_notes': technician_notes,
            'updated_at': datetime.utcnow(),
            'updated_by': ObjectId(current_user.id)
        }},
        return_document=True
    )
    
    if maintenance_request:
        # Send email notification about the status update
        send_maintenance_update(request_id)
        return jsonify({
            "message": "Status updated successfully",
            "status": new_status
        })
    
    return jsonify({"error": "Maintenance request not found"}), 404

if __name__ == '__main__':
    app.run(debug=True) 