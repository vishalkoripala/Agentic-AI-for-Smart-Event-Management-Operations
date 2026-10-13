import os
import json
import re
import atexit
from datetime import datetime, timezone
from typing import Any, ClassVar
from flask import Flask, render_template, redirect, url_for, flash, request, abort, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
# noinspection PyPackageRequirements
from werkzeug.security import generate_password_hash, check_password_hash
import random
# noinspection PyPackageRequirements
from google import genai
# noinspection PyPackageRequirements
from google.genai import types
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or os.urandom(32)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///eventops.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
if not GEMINI_API_KEY:
    print("WARNING: GEMINI_API_KEY not set. Assistant will not work.")

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

class BaseModel(db.Model):
    """Shared model base with Flask-SQLAlchemy's dynamic query descriptor typed."""
    __abstract__ = True
    query: ClassVar[Any]

# ------------------ Models ------------------
class User(UserMixin, BaseModel):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='user')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Event(BaseModel):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    date = db.Column(db.String(50))
    time = db.Column(db.String(50))
    location = db.Column(db.String(200))
    capacity = db.Column(db.Integer)
    status = db.Column(db.String(20), default='upcoming')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class Registration(BaseModel):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'))
    ticket_type = db.Column(db.String(50))
    status = db.Column(db.String(20), default='registered')
    checked_in = db.Column(db.Boolean, default=False)
    registered_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', backref='registrations')
    event = db.relationship('Event', backref='registrations')

class Venue(BaseModel):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    capacity = db.Column(db.Integer)
    utilization = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(20), default='available')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class Speaker(BaseModel):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    bio = db.Column(db.Text)
    session_title = db.Column(db.String(200))
    schedule = db.Column(db.String(100))
    availability = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class Sponsorship(BaseModel):
    id = db.Column(db.Integer, primary_key=True)
    sponsor_name = db.Column(db.String(100), nullable=False)
    commitment = db.Column(db.String(200))
    deliverables = db.Column(db.String(200))
    visibility_score = db.Column(db.Integer, default=0)
    roi = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class Incident(BaseModel):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    priority = db.Column(db.String(20), default='medium')
    status = db.Column(db.String(20), default='open')
    reported_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    resolved_at = db.Column(db.DateTime, nullable=True)

class Notification(BaseModel):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    recipient = db.Column(db.String(100))
    channel = db.Column(db.String(20))
    subject = db.Column(db.String(200))
    message = db.Column(db.Text)
    sent_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    read = db.Column(db.Boolean, default=False)

    user = db.relationship('User', backref='notifications')

class Reminder(BaseModel):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False)
    remind_at = db.Column(db.DateTime, nullable=False)
    message = db.Column(db.Text, nullable=False)
    sent = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', backref='reminders')
    event = db.relationship('Event', backref='reminders')

class Feedback(BaseModel):
    __tablename__ = 'feedback'
    __table_args__ = {'extend_existing': True}
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', backref='feedbacks')
    event = db.relationship('Event', backref='feedbacks')

class ContactMessage(BaseModel):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), nullable=False)
    topic = db.Column(db.String(100), nullable=False, default='general')
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

# ------------------ Login Loader ------------------
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# ------------------ Gemini Assistant ------------------
class GeminiAssistant:
    def __init__(self, user_id):
        self.user_id = user_id
        api_key = os.environ.get('GEMINI_API_KEY')
        if not api_key:
            raise ValueError("GEMINI_API_KEY not set. Please set it in your environment.")
        self.client = genai.Client(api_key=api_key)
        self.model = "gemini-3.6-flash"
        self.system_instruction = """
        You are EventOps AI, an intelligent assistant for event management.
        You can help users with event information, registrations, venues, speakers, and reminders.

        IMPORTANT RULES:
        - The user is ALREADY logged in. You NEVER need to ask for their user ID.
        - When the user asks about "my registrations", "my events", or similar, call get_my_registrations(user_id) with the user_id automatically provided.
        - When the user asks to set a reminder, call schedule_reminder(user_id, event_id, remind_at, message) with user_id automatically provided.
        - For any action that requires a user_id, it is already available and you should include it in the parameters without asking.
        - If you are unsure, just call get_my_registrations(user_id) to show the user's registrations.

        You have access to the following functions (call them by outputting JSON):
        - get_events(): returns list of all events with details.
        - get_my_registrations(user_id): returns events the user has registered for.
        - get_event_details(event_id): returns full details of a specific event.
        - schedule_reminder(user_id, event_id, remind_at, message): schedules a reminder notification.
        - get_venues(): returns venue information.
        - get_speakers(): returns speaker information.

        Always respond in a friendly, helpful tone.

        IMPORTANT: Your response must be a single JSON object with keys "action", "parameters", and "message".
        Do not include any additional text, explanations, or markdown formatting.
        """
        self.functions = {
            'get_events': self.get_events,
            'get_my_registrations': self.get_my_registrations,
            'get_event_details': self.get_event_details,
            'schedule_reminder': self.schedule_reminder,
            'get_venues': self.get_venues,
            'get_speakers': self.get_speakers,
        }

    # ----- Helper methods -----
    @staticmethod
    def get_events():
        events = Event.query.all()
        return [{'id': e.id, 'title': e.title, 'date': e.date, 'time': e.time, 'location': e.location, 'capacity': e.capacity} for e in events]

    @staticmethod
    def get_my_registrations(user_id):
        registrations = Registration.query.filter_by(user_id=user_id).all()
        return [{'event_id': r.event_id, 'event_title': r.event.title, 'ticket_type': r.ticket_type, 'checked_in': r.checked_in} for r in registrations]

    @staticmethod
    def get_event_details(event_id):
        event = db.session.get(Event, event_id)
        if not event:
            return {'error': 'Event not found'}
        return {'id': event.id, 'title': event.title, 'description': event.description, 'date': event.date, 'time': event.time, 'location': event.location, 'capacity': event.capacity, 'status': event.status}

    @staticmethod
    def get_venues():
        venues = Venue.query.all()
        return [{'id': v.id, 'name': v.name, 'capacity': v.capacity, 'utilization': v.utilization, 'status': v.status} for v in venues]

    @staticmethod
    def get_speakers():
        speakers = Speaker.query.all()
        return [{'id': s.id, 'name': s.name, 'session_title': s.session_title, 'schedule': s.schedule, 'availability': s.availability} for s in speakers]

    @staticmethod
    def schedule_reminder(user_id, event_id, remind_at, message):
        try:
            if isinstance(remind_at, str):
                remind_at = datetime.fromisoformat(remind_at.replace('Z', '+00:00'))
            reminder = Reminder(
                user_id=user_id,
                event_id=event_id,
                remind_at=remind_at,
                message=message,
                sent=False
            )
            db.session.add(reminder)
            db.session.commit()
            return {'success': True, 'reminder_id': reminder.id, 'scheduled_for': remind_at.isoformat()}
        except Exception as e:
            return {'error': str(e)}

    def _format_my_registrations(self):
        registrations = self.get_my_registrations(self.user_id)
        if not registrations:
            return "You are not registered for any events."
        return "\n".join(
            f"{item['event_title']} (Ticket: {item['ticket_type']}) - "
            f"Checked in: {'yes' if item['checked_in'] else 'no'}"
            for item in registrations
        )

    # ----- Main processing -----
    def process_query(self, user_message):
        # ---- PRE‑PROCESSING: direct handling for common queries ----
        # 1. My registrations
        if re.search(r'\b(my registrations|my events|my tickets|what am I registered for|registrations\b)', user_message, re.IGNORECASE):
            return self._format_my_registrations()

        # 2. Upcoming events
        if re.search(r'\b(upcoming events|show events|list events|what events are coming)\b', user_message, re.IGNORECASE):
            events = self.get_events()
            if not events:
                return "No events found."
            lines = [f"• {e['title']} on {e['date']} at {e['time']} ({e['location']})" for e in events]
            return "\n".join(lines)

        # 3. Venues
        if re.search(r'\b(venues|rooms|locations)\b', user_message, re.IGNORECASE):
            venues = self.get_venues()
            if not venues:
                return "No venues available."
            lines = [f"• {v['name']} (Capacity: {v['capacity']}) - {v['status']}" for v in venues]
            return "\n".join(lines)

        # 4. Speakers
        if re.search(r'\b(speakers|sessions|who is speaking)\b', user_message, re.IGNORECASE):
            speakers = self.get_speakers()
            if not speakers:
                return "No speakers scheduled."
            lines = [f"• {s['name']} - {s['session_title']} ({s['schedule']})" for s in speakers]
            return "\n".join(lines)

        # ---- If not matched, use Gemini ----
        prompt = f"""
        You are an assistant for an event management platform.
        The user said: "{user_message}"

        Extract the user's intent and respond with a JSON object containing:
        - "action": one of ["get_events", "get_my_registrations", "get_event_details", "schedule_reminder", "get_venues", "get_speakers", "answer"]
        - "parameters": a dictionary with necessary parameters (event name, time, message, etc.)
        - "message": if action is "answer", provide a helpful response directly.

        For "get_my_registrations", you do NOT need the user to provide a user_id – it is already known.
        For "schedule_reminder", extract event name, remind time (as ISO datetime), and reminder message.
        For "get_event_details", extract event name or ID.
        If you cannot determine the intent, set action to "answer" and provide a helpful response.

        IMPORTANT: NEVER ask the user for their user ID. The user ID is already known.
        Return ONLY the JSON object, no other text.
        """
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=self.system_instruction,
                    temperature=0.2,
                )
            )
        except Exception as e:
            return f"❌ Gemini error: {str(e)}"

        raw = (response.text or "").strip()
        if not raw:
            return "The assistant returned an empty response. Please try again."
        raw = re.sub(r'^```json\s*|```$', '', raw, flags=re.MULTILINE).strip()

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r'\{.*}', raw, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group())
                except json.JSONDecodeError:
                    return "I'm sorry, I couldn't understand that. Could you please rephrase?"
            else:
                return "I'm sorry, I couldn't understand that. Could you please rephrase?"

        if not isinstance(parsed, dict):
            return "I'm sorry, I couldn't understand that. Could you please rephrase?"
        action = parsed.get('action')
        if not isinstance(action, str):
            return "I'm sorry, I couldn't understand that. Could you please rephrase?"
        params = parsed.get('parameters') or {}
        if not isinstance(params, dict):
            return "I'm sorry, I couldn't understand that. Could you please rephrase?"

        if action == 'answer':
            msg = parsed.get('message', '')
            if not isinstance(msg, str):
                return "I'm sorry, I couldn't understand that. Could you please rephrase?"
            if 'user id' in msg.lower() or 'user_id' in msg.lower():
                return self._format_my_registrations()
            return msg

        func = self.functions.get(action)
        if not func:
            return f"Sorry, I cannot perform '{action}' yet."

        if action in ['get_my_registrations', 'schedule_reminder']:
            if 'user_id' not in params:
                params['user_id'] = self.user_id

        if action in ['get_event_details', 'schedule_reminder']:
            event_name = params.get('event_name')
            if event_name:
                event = Event.query.filter(Event.title.ilike(f'%{event_name}%')).first()
                if event:
                    params['event_id'] = event.id
                else:
                    return f"I couldn't find an event matching '{event_name}'. Please check the name."
            if 'event_name' in params:
                del params['event_name']
            if 'event_id' not in params:
                return "I need an event ID or name to proceed."

        result = func(**params)

        if isinstance(result, list):
            if not result:
                return "No results found."
            lines = []
            for item in result:
                if 'title' in item and 'date' in item:
                    lines.append(f"• {item['title']} on {item.get('date', 'TBD')} at {item.get('time', 'TBD')} ({item.get('location', 'TBD')})")
                elif 'event_title' in item:
                    lines.append(f"• {item['event_title']} (Ticket: {item['ticket_type']}) - Checked in: {'✅' if item['checked_in'] else '❌'}")
                elif 'name' in item:
                    lines.append(f"• {item['name']} - {item.get('session_title', '')} {item.get('schedule', '')}")
                else:
                    lines.append(str(item))
            return "\n".join(lines)
        elif isinstance(result, dict):
            if 'error' in result:
                return f"Error: {result['error']}"
            if 'success' in result:
                return f"✅ Reminder scheduled for {result.get('scheduled_for', '')}."
            return "\n".join([f"{k}: {v}" for k, v in result.items()])
        else:
            return str(result)

# ------------------ Scheduler for Reminders ------------------
def process_reminders():
    with app.app_context():
        now = datetime.now(timezone.utc)
        reminders = Reminder.query.filter(Reminder.remind_at <= now, Reminder.sent == False).all()
        for reminder in reminders:
            user = db.session.get(User, reminder.user_id)
            if user:
                notification = Notification(
                    user_id=user.id,
                    recipient=user.email,
                    channel='email',
                    subject='Event Reminder',
                    message=reminder.message,
                    sent_at=datetime.now(timezone.utc),
                    read=False
                )
                db.session.add(notification)
                reminder.sent = True
        db.session.commit()

# Start background scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(func=process_reminders, trigger="interval", seconds=60)
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

# ------------------ Helper: Mock Data Generation ------------------
def init_db():
    if User.query.count() == 0:
        admin = User(name='Admin User', email='admin@eventops.com', role='admin')
        admin.set_password('admin123')
        user = User(name='Regular User', email='user@eventops.com', role='user')
        user.set_password('user123')
        db.session.add_all([admin, user])
        db.session.commit()

        # --- Create Events ---
        events = [
            Event(title='AI Summit 2026', description='The future of AI in business',
                  date='2026-07-10', time='09:00 AM', location='Main Hall', capacity=200, status='upcoming'),
            Event(title='Hackathon Night', description='24-hour coding challenge',
                  date='2026-07-15', time='06:00 PM', location='Room B', capacity=50, status='upcoming'),
            Event(title='Sponsor Meet & Greet', description='Networking with sponsors',
                  date='2026-07-20', time='11:00 AM', location='Exhibition Hall', capacity=100, status='upcoming'),
        ]
        for e in events:
            db.session.add(e)
        db.session.commit()

        # --- Registrations ---
        admin_user = User.query.filter_by(email='admin@eventops.com').first()
        reg_user = User.query.filter_by(email='user@eventops.com').first()
        if admin_user and reg_user:
            reg1 = Registration(user_id=admin_user.id, event_id=1, ticket_type='VIP')
            reg2 = Registration(user_id=reg_user.id, event_id=2, ticket_type='Standard')
            db.session.add_all([reg1, reg2])
            for _ in range(3):
                db.session.add(Registration(
                    user_id=random.choice([admin_user.id, reg_user.id]),
                    event_id=random.randint(1, 3),
                    ticket_type=random.choice(['Standard', 'VIP'])
                ))
            db.session.commit()

        # --- Venues ---
        venues = ['Main Hall', 'Room A', 'Room B', 'Exhibition Hall', 'Breakout Area']
        for v in venues:
            db.session.add(Venue(name=v, capacity=random.randint(50, 500),
                                 utilization=round(random.uniform(0.2, 0.9), 2)))
        # --- Speakers ---
        speakers = [
            ('Dr. Smith', 'AI Researcher', 'Keynote: Future of AI', 'Day 1 9:00'),
            ('Prof. Lee', 'Data Scientist', 'Data Ethics', 'Day 1 11:00'),
            ('Ms. Jones', 'Event Manager', 'Operational Excellence', 'Day 2 10:00')
        ]
        for name, bio, session, schedule in speakers:
            db.session.add(Speaker(name=name, bio=bio, session_title=session, schedule=schedule))
        # --- Sponsorships ---
        sponsors = [
            ('Acme Corp', '$50k', 'Logo + booth', 85, 1.2),
            ('Beta Inc', '$30k', 'Session sponsor', 70, 0.9),
            ('Gamma Ltd', '$20k', 'Networking sponsor', 60, 0.7)
        ]
        for s, c, d, vs, roi in sponsors:
            db.session.add(Sponsorship(sponsor_name=s, commitment=c, deliverables=d,
                                       visibility_score=vs, roi=roi))
        # --- Incidents ---
        incidents = [
            ('Wi-Fi outage', 'Main hall connectivity lost', 'high'),
            ('Speaker delay', 'Keynote speaker is late', 'medium'),
            ('Catering shortage', 'Lunch not enough for attendees', 'low')
        ]
        for title, desc, priority in incidents:
            db.session.add(Incident(title=title, description=desc, priority=priority))
        # --- Notifications ---
        notifications = [
            ('admin@eventops.com', 'email', 'Welcome', 'Welcome to EventOps AI'),
            ('user@eventops.com', 'sms', 'Event Reminder', 'Your event starts tomorrow')
        ]
        for rec, ch, sub, msg in notifications:
            db.session.add(Notification(recipient=rec, channel=ch, subject=sub, message=msg))
        db.session.commit()

# ------------------ Routes ------------------
@app.route('/')
def index():
    events = (
        Event.query.filter_by(status='upcoming')
        .order_by(Event.date.asc())
        .limit(6)
        .all()
    )
    stats = {
        'events': Event.query.count(),
        'registrations': Registration.query.filter_by(status='registered').count(),
        'speakers': Speaker.query.count(),
        'sponsors': Sponsorship.query.count(),
    }
    return render_template('index.html', events=events, stats=stats)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        user = User.query.filter_by(email=email).first() if email else None
        if isinstance(user, User) and password and user.check_password(password):
            login_user(user)
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        flash('Invalid email or password', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out', 'info')
    return redirect(url_for('index'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not name or not email or not password:
            flash('Please complete all required fields.', 'danger')
        elif len(name) > 100 or len(email) > 100:
            flash('Name and email must be 100 characters or fewer.', 'danger')
        elif not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
            flash('Enter a valid email address.', 'danger')
        elif len(password) < 8:
            flash('Choose a password with at least 8 characters.', 'danger')
        elif password != confirm_password:
            flash('The passwords do not match.', 'danger')
        elif User.query.filter_by(email=email).first():
            flash('Email already registered', 'danger')
        else:
            user = User(name=name, email=email, role='user')
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash('Registration successful! Please login.', 'success')
            return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/dashboard')
@login_required
def dashboard():
    stats = {
        'registrations': Registration.query.count(),
        'venues': Venue.query.count(),
        'speakers': Speaker.query.count(),
        'sponsorships': Sponsorship.query.count(),
    }
    user_regs = Registration.query.filter_by(user_id=current_user.id).all()
    my_reg_count = len(user_regs)
    checked_in_count = sum(1 for r in user_regs if r.checked_in)
    upcoming_events_count = Event.query.filter(Event.status == 'upcoming').count()

    stats['my_registrations'] = my_reg_count
    stats['checked_in'] = checked_in_count
    stats['upcoming_events'] = upcoming_events_count

    recent_incidents = Incident.query.order_by(Incident.reported_at.desc()).limit(5).all()
    recent_activities = [
        {'icon': 'person-plus', 'color': 'primary', 'text': 'John Doe registered for AI Summit 2026', 'time': '5 min ago'},
        {'icon': 'building', 'color': 'success', 'text': 'Main Hall venue utilization updated to 78%', 'time': '18 min ago'},
        {'icon': 'mic', 'color': 'warning', 'text': 'Dr. Smith\'s session rescheduled to 2:00 PM', 'time': '1 hour ago'},
        {'icon': 'trophy', 'color': 'purple', 'text': 'Acme Corp sponsorship deliverables completed', 'time': '2 hours ago'},
        {'icon': 'exclamation-triangle', 'color': 'danger', 'text': 'Wi-Fi outage incident resolved', 'time': '3 hours ago'},
    ]
    return render_template('dashboard.html',
                           stats=stats,
                           my_registrations=user_regs,
                           recent_activities=recent_activities,
                           recent_incidents=recent_incidents)

@app.route('/profile')
@login_required
def profile():
    user_regs = Registration.query.filter_by(user_id=current_user.id).all()
    total_regs = len(user_regs)
    checked_in = sum(1 for r in user_regs if r.checked_in)
    pending = total_regs - checked_in
    stats = {
        'total_registrations': total_regs,
        'checked_in': checked_in,
        'pending': pending,
    }
    return render_template('profile.html', stats=stats)

# ------------------ User Event Routes ------------------
@app.route('/user/register', methods=['GET', 'POST'])
@login_required
def user_register_event():
    events = Event.query.filter_by(status='upcoming').order_by(Event.date.asc()).all()
    if request.method == 'POST':
        event_id = request.form.get('event_id', type=int)
        ticket_type = request.form.get('ticket_type', 'Standard')
        if not event_id:
            flash('Please select an event.', 'danger')
        else:
            event = db.session.get(Event, event_id)
            if not event:
                flash('That event could not be found.', 'danger')
            elif event.status != 'upcoming':
                flash('Registration is only available for upcoming events.', 'warning')
            elif ticket_type not in {'Standard', 'VIP'}:
                flash('Choose a valid ticket type.', 'danger')
            elif Registration.query.filter_by(user_id=current_user.id, event_id=event_id).first():
                flash('You are already registered for this event.', 'warning')
            elif event.capacity is not None and Registration.query.filter_by(event_id=event_id).count() >= event.capacity:
                flash('This event has reached capacity.', 'warning')
            else:
                reg = Registration(user_id=current_user.id, event_id=event_id, ticket_type=ticket_type)
                db.session.add(reg)
                db.session.commit()
                flash('Successfully registered for the event!', 'success')
                return redirect(url_for('dashboard'))
    return render_template('user_register_event.html', events=events)

@app.route('/event/<int:event_id>/register', methods=['GET', 'POST'])
@login_required
def register_for_event(event_id):
    event = Event.query.get_or_404(event_id)
    if request.method == 'POST':
        ticket_type = request.form.get('ticket_type', 'Standard')
        if event.status != 'upcoming':
            flash('Registration is only available for upcoming events.', 'warning')
        elif ticket_type not in {'Standard', 'VIP'}:
            flash('Choose a valid ticket type.', 'danger')
        elif Registration.query.filter_by(user_id=current_user.id, event_id=event_id).first():
            flash('You are already registered for this event.', 'warning')
        elif event.capacity is not None and Registration.query.filter_by(event_id=event_id).count() >= event.capacity:
            flash('This event has reached capacity.', 'warning')
        else:
            reg = Registration(user_id=current_user.id, event_id=event_id, ticket_type=ticket_type)
            db.session.add(reg)
            db.session.commit()
            flash('Successfully registered for the event!', 'success')
            return redirect(url_for('event_detail', event_id=event_id))
    return render_template('event_register.html', event=event)

@app.route('/user/registrations')
@login_required
def user_my_registrations():
    registrations = Registration.query.filter_by(user_id=current_user.id).all()
    return render_template('user_registrations.html', registrations=registrations)
# ----- EDIT & DELETE routes (already here) -----
@app.route('/user/registrations/cancel/<int:reg_id>', methods=['POST'])
@login_required
def cancel_registration(reg_id):
    """Cancel (delete) a user's registration."""
    reg = Registration.query.get_or_404(reg_id)
    if reg.user_id != current_user.id:
        abort(403)
    event_title = reg.event.title
    db.session.delete(reg)
    db.session.commit()
    flash(f'Your registration for "{event_title}" has been cancelled.', 'success')
    return redirect(url_for('user_my_registrations'))

@app.route('/user/registrations/edit/<int:reg_id>', methods=['POST'])
@login_required
def edit_registration(reg_id):
    """Change the ticket type for a registration."""
    reg = Registration.query.get_or_404(reg_id)
    if reg.user_id != current_user.id:
        abort(403)
    new_ticket_type = request.form.get('ticket_type')
    if new_ticket_type in {'Standard', 'VIP'}:
        reg.ticket_type = new_ticket_type
        db.session.commit()
        flash(f'Ticket type updated to "{new_ticket_type}".', 'success')
    else:
        flash('Invalid ticket type.', 'danger')
    return redirect(url_for('user_my_registrations'))

@app.route('/events')
@login_required
def browse_events():
    events = Event.query.all()
    return render_template('browse_events.html', events=events)

@app.route('/event/<int:event_id>')
@login_required
def event_detail(event_id):
    event = Event.query.get_or_404(event_id)
    registered = Registration.query.filter_by(user_id=current_user.id, event_id=event_id).first() is not None
    return render_template('event_detail.html', event=event, registered=registered)

# ------------------ Assistant Routes ------------------
@app.route('/assistant')
@login_required
def assistant_page():
    return render_template('assistant.html', now=datetime.now(timezone.utc))

# noinspection PyBroadException
@app.route('/api/assistant', methods=['POST'])
@login_required
def assistant_api():
    payload = request.get_json(silent=True) or {}
    user_message = payload.get('message')
    if not isinstance(user_message, str) or not user_message.strip():
        return jsonify({'error': 'Message is required'}), 400
    if not GEMINI_API_KEY:
        return jsonify({'error': 'Assistant is unavailable until GEMINI_API_KEY is configured.'}), 503

    try:
        assistant = GeminiAssistant(current_user.id)
        reply = assistant.process_query(user_message)
        return jsonify({'reply': reply})
    except Exception:
        app.logger.exception('Assistant request failed')
        return jsonify({'error': 'The assistant could not process your request.'}), 500

# ------------------ Admin Routes ------------------
@app.route('/admin/events')
@login_required
def admin_events():
    if current_user.role != 'admin':
        abort(403)
    events = Event.query.all()
    return render_template('admin_events.html', events=events)

@app.route('/admin/events/add', methods=['GET', 'POST'])
@login_required
def admin_event_add():
    if current_user.role != 'admin':
        abort(403)
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        capacity_raw = request.form.get('capacity', '').strip()
        capacity = request.form.get('capacity', type=int)
        status = request.form.get('status', 'upcoming').strip()
        if not title:
            flash('Event title is required.', 'danger')
        elif capacity_raw and (capacity is None or capacity < 1):
            flash('Capacity must be a positive whole number.', 'danger')
        elif status not in {'upcoming', 'ongoing', 'completed'}:
            flash('Choose a valid event status.', 'danger')
        else:
            event = Event(
                title=title,
                description=request.form.get('description'),
                date=request.form.get('date'),
                time=request.form.get('time'),
                location=request.form.get('location'),
                capacity=capacity,
                status=status
            )
            db.session.add(event)
            db.session.commit()
            flash('Event added!', 'success')
            return redirect(url_for('admin_events'))
    return render_template('admin_event_form.html', event=None)

@app.route('/admin/events/edit/<int:event_id>', methods=['GET', 'POST'])
@login_required
def admin_event_edit(event_id):
    if current_user.role != 'admin':
        abort(403)
    event = Event.query.get_or_404(event_id)
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        capacity_raw = request.form.get('capacity', '').strip()
        capacity = request.form.get('capacity', type=int)
        status = request.form.get('status', event.status).strip()
        if not title:
            flash('Event title is required.', 'danger')
            return render_template('admin_event_form.html', event=event)
        if capacity_raw and (capacity is None or capacity < 1):
            flash('Capacity must be a positive whole number.', 'danger')
            return render_template('admin_event_form.html', event=event)
        if status not in {'upcoming', 'ongoing', 'completed'}:
            flash('Choose a valid event status.', 'danger')
            return render_template('admin_event_form.html', event=event)
        event.title = title
        event.description = request.form.get('description')
        event.date = request.form.get('date')
        event.time = request.form.get('time')
        event.location = request.form.get('location')
        event.capacity = capacity
        event.status = status
        db.session.commit()
        flash('Event updated!', 'success')
        return redirect(url_for('admin_events'))
    return render_template('admin_event_form.html', event=event)

@app.route('/admin/events/delete/<int:event_id>', methods=['POST'])
@login_required
def admin_event_delete(event_id):
    if current_user.role != 'admin':
        abort(403)
    event = Event.query.get_or_404(event_id)
    if Registration.query.filter_by(event_id=event.id).first():
        flash('This event has registrations and cannot be deleted. Mark it completed to keep its history.', 'warning')
    else:
        db.session.delete(event)
        db.session.commit()
        flash('Event deleted.', 'success')
    return redirect(url_for('admin_events'))

@app.route('/admin/registrations')
@login_required
def admin_registrations():
    if current_user.role != 'admin':
        abort(403)
    registrations = Registration.query.all()
    return render_template('admin_registrations.html', registrations=registrations)

@app.route('/admin/registrations/checkin/<int:reg_id>', methods=['POST'])
@login_required
def admin_checkin(reg_id):
    if current_user.role != 'admin':
        abort(403)
    reg = Registration.query.get_or_404(reg_id)
    reg.checked_in = not reg.checked_in
    db.session.commit()
    flash(f'Check-in status updated for {reg.event.title}', 'success')
    return redirect(url_for('admin_registrations'))

@app.route('/admin/venues')
@login_required
def admin_venues():
    if current_user.role != 'admin':
        abort(403)
    venues = Venue.query.all()
    return render_template('admin_venues.html', venues=venues)

@app.route('/admin/venues/add', methods=['GET', 'POST'])
@login_required
def admin_venue_add():
    if current_user.role != 'admin':
        abort(403)
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        capacity_raw = request.form.get('capacity', '').strip()
        capacity = request.form.get('capacity', type=int)
        if not name:
            flash('Venue name is required.', 'danger')
        elif capacity_raw and (capacity is None or capacity < 1):
            flash('Capacity must be a positive whole number.', 'danger')
        else:
            venue = Venue(name=name, capacity=capacity)
            db.session.add(venue)
            db.session.commit()
            flash('Venue added', 'success')
            return redirect(url_for('admin_venues'))
    return render_template('admin_venue_form.html', venue=None)

@app.route('/admin/venues/edit/<int:venue_id>', methods=['GET', 'POST'])
@login_required
def admin_venue_edit(venue_id):
    if current_user.role != 'admin':
        abort(403)
    venue = Venue.query.get_or_404(venue_id)
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        capacity_raw = request.form.get('capacity', '').strip()
        capacity = request.form.get('capacity', type=int)
        utilization_raw = request.form.get('utilization', '').strip()
        utilization = request.form.get('utilization', type=float)
        status = request.form.get('status', '').strip()
        if not name:
            flash('Venue name is required.', 'danger')
            return render_template('admin_venue_form.html', venue=venue)
        if capacity_raw and (capacity is None or capacity < 1):
            flash('Capacity must be a positive whole number.', 'danger')
            return render_template('admin_venue_form.html', venue=venue)
        if utilization_raw and (utilization is None or not 0 <= utilization <= 1):
            flash('Utilization must be between 0 and 1.', 'danger')
            return render_template('admin_venue_form.html', venue=venue)
        if status not in {'available', 'reserved', 'maintenance'}:
            flash('Choose a valid venue status.', 'danger')
            return render_template('admin_venue_form.html', venue=venue)
        venue.name = name
        venue.capacity = capacity
        venue.utilization = utilization or 0.0
        venue.status = status
        db.session.commit()
        flash('Venue updated', 'success')
        return redirect(url_for('admin_venues'))
    return render_template('admin_venue_form.html', venue=venue)

@app.route('/admin/venues/delete/<int:venue_id>', methods=['POST'])
@login_required
def admin_venue_delete(venue_id):
    if current_user.role != 'admin':
        abort(403)
    venue = Venue.query.get_or_404(venue_id)
    db.session.delete(venue)
    db.session.commit()
    flash('Venue deleted', 'success')
    return redirect(url_for('admin_venues'))

@app.route('/admin/speakers')
@login_required
def admin_speakers():
    if current_user.role != 'admin':
        abort(403)
    speakers = Speaker.query.all()
    return render_template('admin_speakers.html', speakers=speakers)

@app.route('/admin/speakers/add', methods=['GET', 'POST'])
@login_required
def admin_speaker_add():
    if current_user.role != 'admin':
        abort(403)
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if name:
            speaker = Speaker(name=name, bio=request.form.get('bio'),
                              session_title=request.form.get('session_title'),
                              schedule=request.form.get('schedule'),
                              availability=request.form.get('availability') == 'on')
            db.session.add(speaker)
            db.session.commit()
            flash('Speaker added', 'success')
            return redirect(url_for('admin_speakers'))
        flash('Speaker name is required.', 'danger')
    return render_template('admin_speaker_form.html', speaker=None)

@app.route('/admin/speakers/edit/<int:speaker_id>', methods=['GET', 'POST'])
@login_required
def admin_speaker_edit(speaker_id):
    if current_user.role != 'admin':
        abort(403)
    speaker = Speaker.query.get_or_404(speaker_id)
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Speaker name is required.', 'danger')
            return render_template('admin_speaker_form.html', speaker=speaker)
        speaker.name = name
        speaker.bio = request.form.get('bio')
        speaker.session_title = request.form.get('session_title')
        speaker.schedule = request.form.get('schedule')
        speaker.availability = request.form.get('availability') == 'on'
        db.session.commit()
        flash('Speaker updated', 'success')
        return redirect(url_for('admin_speakers'))
    return render_template('admin_speaker_form.html', speaker=speaker)

@app.route('/admin/speakers/delete/<int:speaker_id>', methods=['POST'])
@login_required
def admin_speaker_delete(speaker_id):
    if current_user.role != 'admin':
        abort(403)
    speaker = Speaker.query.get_or_404(speaker_id)
    db.session.delete(speaker)
    db.session.commit()
    flash('Speaker deleted', 'success')
    return redirect(url_for('admin_speakers'))

@app.route('/admin/sponsorships')
@login_required
def admin_sponsorships():
    if current_user.role != 'admin':
        abort(403)
    sponsorships = Sponsorship.query.all()
    return render_template('admin_sponsorships.html', sponsorships=sponsorships)

@app.route('/admin/sponsorships/add', methods=['GET', 'POST'])
@login_required
def admin_sponsorship_add():
    if current_user.role != 'admin':
        abort(403)
    if request.method == 'POST':
        name = request.form.get('sponsor_name', '').strip()
        if name:
            visibility_score = request.form.get('visibility_score', 0, type=int)
            roi = request.form.get('roi', 0.0, type=float)
            sp = Sponsorship(sponsor_name=name, commitment=request.form.get('commitment'),
                             deliverables=request.form.get('deliverables'),
                             visibility_score=max(0, min(100, visibility_score or 0)),
                             roi=max(0.0, roi or 0.0))
            db.session.add(sp)
            db.session.commit()
            flash('Sponsorship added', 'success')
            return redirect(url_for('admin_sponsorships'))
        flash('Sponsor name is required.', 'danger')
    return render_template('admin_sponsorship_form.html', sponsorship=None)

@app.route('/admin/sponsorships/edit/<int:sponsorship_id>', methods=['GET', 'POST'])
@login_required
def admin_sponsorship_edit(sponsorship_id):
    if current_user.role != 'admin':
        abort(403)
    sp = Sponsorship.query.get_or_404(sponsorship_id)
    if request.method == 'POST':
        sponsor_name = request.form.get('sponsor_name', '').strip()
        if not sponsor_name:
            flash('Sponsor name is required.', 'danger')
            return render_template('admin_sponsorship_form.html', sponsorship=sp)
        visibility_score = request.form.get('visibility_score', 0, type=int)
        roi = request.form.get('roi', 0.0, type=float)
        sp.sponsor_name = sponsor_name
        sp.commitment = request.form.get('commitment')
        sp.deliverables = request.form.get('deliverables')
        sp.visibility_score = max(0, min(100, visibility_score or 0))
        sp.roi = max(0.0, roi or 0.0)
        db.session.commit()
        flash('Sponsorship updated', 'success')
        return redirect(url_for('admin_sponsorships'))
    return render_template('admin_sponsorship_form.html', sponsorship=sp)

@app.route('/admin/sponsorships/delete/<int:sponsorship_id>', methods=['POST'])
@login_required
def admin_sponsorship_delete(sponsorship_id):
    if current_user.role != 'admin':
        abort(403)
    sp = Sponsorship.query.get_or_404(sponsorship_id)
    db.session.delete(sp)
    db.session.commit()
    flash('Sponsorship deleted', 'success')
    return redirect(url_for('admin_sponsorships'))
@app.route('/admin/incidents')
@login_required
def admin_incidents():
    if current_user.role != 'admin':
        abort(403)
    incidents = Incident.query.all()
    now = datetime.now(timezone.utc)  # timezone-aware

    for inc in incidents:
        # Ensure reported_at is also timezone-aware
        if inc.reported_at.tzinfo is None:
            inc.reported_at = inc.reported_at.replace(tzinfo=timezone.utc)

        delta = now - inc.reported_at
        seconds = delta.total_seconds()
        if seconds < 60:
            inc.time_ago = f"{int(seconds)}s ago"
        elif seconds < 3600:
            inc.time_ago = f"{int(seconds // 60)}m ago"
        elif seconds < 86400:
            inc.time_ago = f"{int(seconds // 3600)}h ago"
        else:
            inc.time_ago = f"{int(seconds // 86400)}d ago"

    return render_template('admin_incidents.html', incidents=incidents, now=now)

@app.route('/admin/incidents/add', methods=['GET', 'POST'])
@login_required
def admin_incident_add():
    if current_user.role != 'admin':
        abort(403)
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        priority = request.form.get('priority', 'medium').strip().lower()
        if title and priority in {'low', 'medium', 'high'}:
            inc = Incident(title=title, description=request.form.get('description'),
                           priority=priority)
            db.session.add(inc)
            db.session.commit()
            flash('Incident added', 'success')
            return redirect(url_for('admin_incidents'))
        flash('Enter an incident title and choose a valid priority.', 'danger')
    return render_template('admin_incident_form.html', incident=None)

@app.route('/admin/incidents/edit/<int:incident_id>', methods=['GET', 'POST'])
@login_required
def admin_incident_edit(incident_id):
    if current_user.role != 'admin':
        abort(403)
    inc = Incident.query.get_or_404(incident_id)
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        priority = request.form.get('priority', 'medium').strip().lower()
        status = request.form.get('status', 'open').strip().lower()
        if not title or priority not in {'low', 'medium', 'high'} or status not in {'open', 'in_progress', 'resolved'}:
            flash('Enter an incident title and choose valid priority and status values.', 'danger')
            return render_template('admin_incident_form.html', incident=inc)
        inc.title = title
        inc.description = request.form.get('description')
        inc.priority = priority
        inc.status = status
        if inc.status == 'resolved' and not inc.resolved_at:
            inc.resolved_at = datetime.now(timezone.utc)
        elif inc.status != 'resolved':
            inc.resolved_at = None
        db.session.commit()
        flash('Incident updated', 'success')
        return redirect(url_for('admin_incidents'))
    return render_template('admin_incident_form.html', incident=inc)

@app.route('/admin/incidents/delete/<int:incident_id>', methods=['POST'])
@login_required
def admin_incident_delete(incident_id):
    if current_user.role != 'admin':
        abort(403)
    inc = Incident.query.get_or_404(incident_id)
    db.session.delete(inc)
    db.session.commit()
    flash('Incident deleted', 'success')
    return redirect(url_for('admin_incidents'))

@app.route('/admin/intelligence')
@login_required
def admin_intelligence():
    if current_user.role != 'admin':
        abort(403)
    now = datetime.now()
    context = {
        'health_score': 85,
        'last_updated': now.strftime('%H:%M:%S'),
        'active_agents': 5,
        'total_agents': 5,
        'open_incidents': 2,
        'resolved_incidents': 12,
        'total_registrations': 340,
        'checked_in': 287,
        'avg_response_time': 1.4,
        'response_trend': -5,
        'upcoming_sessions': 3,
        'next_session_time': '2:30 PM',
        'sponsor_roi': 78,
        'sponsor_count': 6,
        'task_completion': 92,
        'tasks_completed': 184,
        'tasks_total': 200,
        'agent_scores': {
            'Registration': 92,
            'Venue': 78,
            'Speaker': 65,
            'Sponsorship': 88,
            'Incident': 55
        },
        'agents': [
            {'name': 'Registration', 'status': 'active', 'score': 92,
             'tasks_processed': 345, 'success_rate': 97,
             'last_activity': '10:32', 'uptime': '12h 14m',
             'memory': '128 MB', 'cpu': '12%'},
            {'name': 'Venue', 'status': 'active', 'score': 78,
             'tasks_processed': 210, 'success_rate': 91,
             'last_activity': '10:15', 'uptime': '12h 05m',
             'memory': '96 MB', 'cpu': '8%'},
            {'name': 'Speaker', 'status': 'idle', 'score': 65,
             'tasks_processed': 89, 'success_rate': 85,
             'last_activity': '09:45', 'uptime': '11h 30m',
             'memory': '64 MB', 'cpu': '3%'},
            {'name': 'Sponsorship', 'status': 'active', 'score': 88,
             'tasks_processed': 156, 'success_rate': 94,
             'last_activity': '10:20', 'uptime': '12h 10m',
             'memory': '80 MB', 'cpu': '7%'},
            {'name': 'Incident', 'status': 'active', 'score': 55,
             'tasks_processed': 67, 'success_rate': 72,
             'last_activity': '10:00', 'uptime': '11h 45m',
             'memory': '112 MB', 'cpu': '15%'},
        ],
        'health_trend': {
            'labels': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
            'values': [82, 84, 80, 78, 83, 85, 85]
        },
    }
    return render_template('admin_intelligence.html', **context)

@app.route('/admin/notifications')
@login_required
def admin_notifications():
    if current_user.role != 'admin':
        abort(403)
    notifications = Notification.query.order_by(Notification.sent_at.desc()).all()
    return render_template('admin_notifications.html', notifications=notifications)

@app.route('/admin/notifications/send', methods=['GET', 'POST'])
@login_required
def admin_notification_send():
    if current_user.role != 'admin':
        abort(403)
    if request.method == 'POST':
        recipient = request.form.get('recipient', '').strip()
        channel = request.form.get('channel', '').strip().lower()
        subject = request.form.get('subject', '').strip()
        message = request.form.get('message', '').strip()
        if recipient and subject and message and channel in {'email', 'sms', 'push'}:
            notif = Notification(recipient=recipient, channel=channel, subject=subject, message=message)
            db.session.add(notif)
            db.session.commit()
            flash('Notification record saved.', 'success')
            return redirect(url_for('admin_notifications'))
        flash('Enter a recipient, a valid channel, a subject, and a message.', 'danger')
    return render_template('admin_notification_form.html')


@app.route('/admin/feedback')
@login_required
def admin_feedback():
    if current_user.role != 'admin':
        abort(403)
    feedbacks = Feedback.query.order_by(Feedback.created_at.desc()).all()
    return render_template('admin_feedback.html', feedbacks=feedbacks)

@app.route('/admin/feedback/delete/<int:feedback_id>', methods=['POST'])
@login_required
def admin_feedback_delete(feedback_id):
    if current_user.role != 'admin':
        abort(403)
    feedback_entry = Feedback.query.get_or_404(feedback_id)
    db.session.delete(feedback_entry)
    db.session.commit()
    flash('Feedback deleted.', 'success')
    return redirect(url_for('admin_feedback'))

@app.route('/admin/contact-messages')
@login_required
def admin_contact_messages():
    if current_user.role != 'admin':
        abort(403)
    messages = ContactMessage.query.order_by(ContactMessage.created_at.desc()).all()
    return render_template('admin_contact_messages.html', messages=messages)

# USER FEEDBACK ROUTE
@app.route('/feedback/<int:event_id>', methods=['GET', 'POST'])
@login_required
def feedback(event_id):
    event = Event.query.get_or_404(event_id)
    existing = Feedback.query.filter_by(user_id=current_user.id, event_id=event_id).first()
    if request.method == 'POST':
        rating = request.form.get('rating', type=int)
        comment = request.form.get('comment', '').strip()
        if not rating or rating < 1 or rating > 5:
            flash('Please select a rating (1-5).', 'danger')
            return render_template('feedback.html', event=event, existing=existing)
        if existing:
            existing.rating = rating
            existing.comment = comment
            db.session.commit()
            flash('Your feedback has been updated.', 'success')
        else:
            fb = Feedback(user_id=current_user.id, event_id=event_id, rating=rating, comment=comment)
            db.session.add(fb)
            db.session.commit()
            flash('Thank you for your feedback!', 'success')
        return redirect(url_for('event_detail', event_id=event_id))
    return render_template('feedback.html', event=event, existing=existing)

@app.route('/my-feedback')
@login_required
def feedback_list():
    feedbacks = Feedback.query.filter_by(user_id=current_user.id).order_by(Feedback.created_at.desc()).all()
    return render_template('feedback_list.html', feedbacks=feedbacks)

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        topic = request.form.get('topic', '').strip()
        message = request.form.get('message', '').strip()
        valid_topics = {'', 'event-operations', 'account-access', 'feedback', 'other'}

        if not name or not email or not message:
            flash('Please complete your name, email address, and message.', 'danger')
        elif len(name) > 100 or len(email) > 100 or len(message) > 5000:
            flash('Please keep your name and email at 100 characters or fewer and your message at 5,000 characters or fewer.', 'danger')
        elif not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
            flash('Enter a valid email address so the team can follow up.', 'danger')
        elif topic not in valid_topics:
            flash('Choose a valid contact topic.', 'danger')
        else:
            contact_message = ContactMessage(
                name=name,
                email=email,
                topic=topic or 'general',
                message=message
            )
            db.session.add(contact_message)
            db.session.commit()
            flash('Thanks! Your message has been added to the EventOps AI contact inbox.', 'success')
            return redirect(url_for('contact'))
        return render_template('contact.html')
    return render_template('contact.html')

@app.route('/about')
def about():
    return render_template('about.html', now=datetime.now(timezone.utc))

# ------------------ Main ------------------
with app.app_context():
    db.create_all()
    init_db()

if __name__ == '__main__':
    app.run(
        host=os.environ.get('HOST', '127.0.0.1'),
        port=int(os.environ.get('PORT', '5000')),
        debug=os.environ.get('FLASK_DEBUG', '').lower() in {'1', 'true'}
    )
