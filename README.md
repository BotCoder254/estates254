# ResidenceHub - Property Management System

A modern property management system built with Flask, MongoDB, and Tailwind CSS.

## Live Demo
Access the live application at: 
- Render: https://residencehub.onrender.com
- Vercel: https://residencehub.vercel.app

Default test credentials:
- Manager Login:
  - Email: manager@example.com
  - Password: test123
- Tenant Login:
  - Email: tenant@example.com
  - Password: test123

## Features

- User Authentication (Tenants & Property Managers)
- Rent Collection & Payment Tracking
- Maintenance Request Management
- Document Management
- Announcements System
- Email Notifications
- Profile Management
- Responsive Design

## Tech Stack

- Backend: Flask (Python)
- Database: MongoDB
- Frontend: HTML, Tailwind CSS, JavaScript
- Email: Flask-Mail
- Authentication: Flask-Login
- Background Tasks: APScheduler
- Deployment: Docker, Render

## Local Development Setup

1. Clone the repository:
```bash
git clone https://github.com/yourusername/residencehub.git
cd residencehub
```

2. Create and activate a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Create a .env file with the following variables:
```env
FLASK_APP=app.py
FLASK_ENV=development
SECRET_KEY=your_secret_key
MONGODB_URI=your_mongodb_uri
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=your_email
MAIL_PASSWORD=your_email_password
```

5. Run the development server:
```bash
flask run
```

## Deployment

### Vercel Deployment

1. Install Vercel CLI:
```bash
npm install -g vercel
```

2. Login to Vercel:
```bash
vercel login
```

3. Configure Environment Variables in Vercel:
   - Go to your Vercel Dashboard
   - Select your project
   - Go to Settings > Environment Variables
   - Add the following variables:
     ```
     FLASK_APP=app.py
     FLASK_ENV=production
     SECRET_KEY=your_secret_key
     MONGODB_URI=your_mongodb_uri
     MAIL_SERVER=smtp.gmail.com
     MAIL_PORT=587
     MAIL_USE_TLS=True
     MAIL_USERNAME=your_email
     MAIL_PASSWORD=your_email_password
     ```

4. Deploy to Vercel:
```bash
vercel
```

For automatic deployments:
- Connect your GitHub repository to Vercel
- Enable automatic deployments
- Push to main branch to trigger deployment

### Render Deployment

1. Create a new Web Service on Render
2. Connect your GitHub repository
3. Use the following settings:
   - Environment: Docker
   - Build Command: `docker build -t residencehub .`
   - Start Command: `docker run -p 8080:8080 residencehub`

4. Add the following environment variables in Render:
   - `FLASK_ENV`
   - `FLASK_APP`
   - `SECRET_KEY`
   - `MONGODB_URI`
   - `MAIL_SERVER`
   - `MAIL_PORT`
   - `MAIL_USERNAME`
   - `MAIL_PASSWORD`
   - `MAIL_USE_TLS`

## Contributing

1. Fork the repository
2. Create a new branch: `git checkout -b feature-name`
3. Make your changes and commit: `git commit -m 'Add feature'`
4. Push to the branch: `git push origin feature-name`
5. Submit a pull request

## License

This project is licensed under the MIT License - see the LICENSE file for details. 