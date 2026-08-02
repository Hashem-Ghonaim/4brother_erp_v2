import os
from supabase import create_client, Client
from werkzeug.utils import secure_filename
import tempfile

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "images")

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print(f"Error initializing Supabase client: {e}")

def upload_file_to_supabase(file_obj, filename, app_config):
    if not supabase:
        # If we are on Vercel, local storage is read-only and will crash
        if os.environ.get('VERCEL') or os.environ.get('VERCEL_URL'):
            return False, "خطأ: مفاتيح رفع الصور (SUPABASE_URL و SUPABASE_KEY) غير موجودة في إعدادات Vercel. يرجى إضافتها وعمل Redeploy."
            
        # Fallback to local storage (for local development)
        try:
            file_path = os.path.join(app_config['UPLOAD_FOLDER'], filename)
            file_obj.save(file_path)
            return True, f"/static/uploads/{filename}"
        except Exception as e:
            return False, f"فشل الحفظ المحلي: {str(e)}"
    
    try:
        # Create a temporary file to save the uploaded content
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, secure_filename(filename))
        file_obj.save(temp_path)
        
        with open(temp_path, "rb") as f:
            res = supabase.storage.from_(SUPABASE_BUCKET).upload(filename, f.read())
            
        os.remove(temp_path)
        
        if getattr(res, 'error', None) and res.error:
             return False, str(res.error)
             
        # Return the public URL
        public_url = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(filename)
        return True, public_url
    except Exception as e:
        return False, str(e)
