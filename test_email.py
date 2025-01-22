from flask import Flask
from flask_mail import Mail, Message
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

# Create Flask app
app = Flask(__name__)

# Configure email settings
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'True').lower() == 'true'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')

# Print configuration for verification
print("Email Configuration:")
print("From:", app.config['MAIL_USERNAME'])
print("Using password:", app.config['MAIL_PASSWORD'])
print("SMTP server:", app.config['MAIL_SERVER'])
print("Port:", app.config['MAIL_PORT'])
print("TLS:", app.config['MAIL_USE_TLS'])

# Initialize Flask-Mail
mail = Mail(app)

def test_send_email():
    try:
        with app.app_context():
            msg = Message(
                'Test Email from Estates254',
                sender=app.config['MAIL_USERNAME'],
                recipients=[app.config['MAIL_USERNAME']]  # Send to self for testing
            )
            msg.body = "This is a test email to verify the email sending functionality."
            mail.send(msg)
            print("Test email sent successfully!")
            return True
    except Exception as e:
        print("Error sending email:", str(e))
        return False

if __name__ == '__main__':
    test_send_email() 