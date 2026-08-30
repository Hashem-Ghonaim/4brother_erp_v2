import os
from datetime import datetime
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
import pytz
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = Flask(__name__, template_folder=TEMPLATES_DIR, static_folder=STATIC_DIR)
app.config['SECRET_KEY'] = 'master_erp_pro_2025'

basedir = BASE_DIR
db_url = os.environ.get('DATABASE_URL')
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url or 'sqlite:///' + os.path.join(BASE_DIR, 'erp_crm.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(STATIC_DIR, 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

try:
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
except OSError:
    pass  # Read-only filesystem (like Vercel)

app.jinja_env.globals.update(now=datetime.now)

@app.template_filter('image_url')
def image_url(filename):
    if not filename:
        return "/static/uploads/default_product.png"
    if str(filename).startswith('http'):
        return filename
    return f"/static/uploads/{filename}"

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# --- Constants ---
FACTORY_LAT = 30.823135
FACTORY_LNG = 31.009878
ALLOWED_RADIUS = 30  # meters
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
SEASON_START = datetime(2025, 1, 1)
SEASON_END = datetime(2026, 7, 15)

def cairo_now():
    cairo_tz = pytz.timezone('Africa/Cairo')
    return datetime.now(cairo_tz).replace(tzinfo=None)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
