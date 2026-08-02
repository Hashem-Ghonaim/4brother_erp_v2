import os
import sys

# Add the project root directory to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app import app

import traceback
@app.errorhandler(500)
def internal_error(e):
    return "<pre>" + traceback.format_exc() + "</pre>", 500

@app.errorhandler(Exception)
def unhandled_exception(e):
    return "<pre>" + traceback.format_exc() + "</pre>", 500

# This is required for Vercel
# The variable name must be 'app'
