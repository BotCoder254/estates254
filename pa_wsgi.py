import os
import sys

# Add your project directory to the sys.path
project_home = '/home/yourusername/residencehub'
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# Load environment variables
from dotenv import load_dotenv
load_dotenv(os.path.join(project_home, '.env'))

# Import your Flask app
from app import app as application 