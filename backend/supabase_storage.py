import os
from supabase import create_client, Client
from werkzeug.utils import secure_filename
import tempfile
import io
import time

def compress_image(file_obj, max_width=1200, quality=75):
    try:
        from PIL import Image
        img = Image.open(file_obj)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        
        if img.width > max_width:
            ratio = max_width / img.width
            new_height = int(img.height * ratio)
            img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
            
        output = io.BytesIO()
        img.save(output, format="WEBP", quality=quality, optimize=True)
        output.seek(0)
        return output, True
    except Exception as e:
        print(f"Image compression failed: {e}")
        file_obj.seek(0)
        return file_obj, False

def upload_file_to_supabase(file_obj, filename, app_config):
    SUPABASE_URL = os.environ.get("SUPABASE_URL")
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
    SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "images")
    
    # Check if image
    is_image = filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.heic'))
    
    if is_image:
        file_obj, compressed = compress_image(file_obj)
        if compressed:
            # Change extension to .webp
            name, _ = os.path.splitext(filename)
            filename = f"{name}.webp"
    
    if not SUPABASE_URL or not SUPABASE_KEY:
        if os.environ.get('VERCEL') or os.environ.get('VERCEL_URL'):
            return False, "خطأ: مفاتيح رفع الصور (SUPABASE_URL و SUPABASE_KEY) غير موجودة في بيئة تشغيل Vercel. تأكد من إضافتها."
            
        # Fallback to local storage
        try:
            file_path = os.path.join(app_config['UPLOAD_FOLDER'], filename)
            if hasattr(file_obj, 'save'):
                file_obj.save(file_path)
            else:
                with open(file_path, 'wb') as f:
                    f.write(file_obj.read())
            return True, f"/static/uploads/{filename}"
        except Exception as e:
            return False, f"فشل الحفظ المحلي: {str(e)}"
    
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        return False, f"خطأ في الاتصال بسيرفر Supabase: {str(e)}"
        
    try:
        temp_dir = tempfile.gettempdir()
        # Add timestamp to avoid collisions
        temp_filename = f"{int(time.time())}_{secure_filename(filename)}"
        temp_path = os.path.join(temp_dir, temp_filename)
        
        if hasattr(file_obj, 'save'):
            file_obj.save(temp_path)
        else:
            with open(temp_path, 'wb') as f:
                f.write(file_obj.read())
        
        # Upload with appropriate content type
        content_type = 'image/webp' if filename.lower().endswith('.webp') else None
        
        with open(temp_path, "rb") as f:
            opts = {"content-type": content_type} if content_type else {}
            res = supabase.storage.from_(SUPABASE_BUCKET).upload(filename, f.read(), file_options=opts)
            
        os.remove(temp_path)
        
        if getattr(res, 'error', None) and res.error:
             return False, str(res.error)
             
        # Return the public URL
        public_url = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(filename)
        return True, public_url
    except Exception as e:
        return False, str(e)
