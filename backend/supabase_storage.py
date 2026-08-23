import os
import time
import tempfile
import io
import cloudinary
import cloudinary.uploader
from supabase import create_client
from werkzeug.utils import secure_filename

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
    '''
    Uploads a file to Cloudinary if configured, otherwise falls back to Supabase.
    (Kept the function name as 'upload_file_to_supabase' to avoid breaking imports across the app)
    '''
    # 1. Cloudinary Configuration
    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME")
    api_key = os.environ.get("CLOUDINARY_API_KEY")
    api_secret = os.environ.get("CLOUDINARY_API_SECRET")
    
    is_image = filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.heic'))
    
    # 2. Upload to Cloudinary (Preferred)
    if cloud_name and api_key and api_secret:
        try:
            cloudinary.config(
                cloud_name = cloud_name,
                api_key = api_key,
                api_secret = api_secret,
                secure = True
            )
            
            # Read file bytes
            if hasattr(file_obj, 'read'):
                file_bytes = file_obj.read()
            else:
                file_bytes = file_obj
                
            upload_result = cloudinary.uploader.upload(
                file_bytes, 
                public_id=f"{int(time.time())}_{os.path.splitext(secure_filename(filename))[0]}",
                folder="erp_uploads",
                resource_type="auto",
                format="webp" if is_image else None, # Force WEBP for images
                quality="auto", # Cloudinary auto compression
                fetch_format="auto" 
            )
            
            return True, upload_result.get("secure_url")
            
        except Exception as e:
            return False, f"خطأ في الرفع إلى Cloudinary: {str(e)}"
            
    # 3. Supabase Configuration (Fallback)
    SUPABASE_URL = os.environ.get("SUPABASE_URL")
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
    SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "images")
    
    if is_image:
        file_obj, compressed = compress_image(file_obj)
        if compressed:
            name, _ = os.path.splitext(filename)
            filename = f"{name}.webp"
            
    if not SUPABASE_URL or not SUPABASE_KEY:
        if os.environ.get('VERCEL') or os.environ.get('VERCEL_URL'):
            return False, "خطأ: مفاتيح رفع الصور غير موجودة في بيئة تشغيل Vercel."
            
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
            
    # Supabase Upload Logic
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        temp_dir = tempfile.gettempdir()
        temp_filename = f"{int(time.time())}_{secure_filename(filename)}"
        temp_path = os.path.join(temp_dir, temp_filename)
        
        if hasattr(file_obj, 'save'):
            file_obj.save(temp_path)
        else:
            with open(temp_path, 'wb') as f:
                f.write(file_obj.read())
        
        content_type = 'image/webp' if filename.lower().endswith('.webp') else None
        
        with open(temp_path, "rb") as f:
            opts = {"content-type": content_type} if content_type else {}
            res = supabase.storage.from_(SUPABASE_BUCKET).upload(filename, f.read(), file_options=opts)
            
        os.remove(temp_path)
        
        if getattr(res, 'error', None) and res.error:
             return False, str(res.error)
             
        public_url = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(filename)
        return True, public_url
    except Exception as e:
        return False, str(e)

