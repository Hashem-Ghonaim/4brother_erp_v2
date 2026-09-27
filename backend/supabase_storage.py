import os
import time
import tempfile
import io
from supabase import create_client
from werkzeug.utils import secure_filename

def compress_image(file_obj, max_width=800, quality=60):
    """
    ضغط الصور بشكل قوي لتقليل حجمها وتوفير مساحة التخزين والباندويث.
    - تحويل لصيغة WebP (أصغر حجماً بنسبة 30-50% من JPEG)
    - تقليل العرض الأقصى لـ 800px (كافي جداً لصور المنتجات في نظام ERP)
    - جودة 60% (توازن ممتاز بين الحجم والوضوح)
    """
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
    رفع الملفات إلى Supabase Storage مباشرة.
    يتم ضغط الصور وتحويلها لـ WebP قبل الرفع لتقليل الحجم والباندويث.
    مع إضافة Cache-Control لمنع إعادة تحميل الصور من السيرفر كل مرة.
    '''
    # --- Supabase Configuration ---
    SUPABASE_URL = os.environ.get("SUPABASE_URL")
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
    SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "images")
    
    is_image = filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.heic'))
    
    # ضغط الصور قبل الرفع
    if is_image:
        file_obj, compressed = compress_image(file_obj)
        if compressed:
            name, _ = os.path.splitext(filename)
            filename = f"{name}.webp"
    
    # --- التأكد من وجود بيانات Supabase ---
    if not SUPABASE_URL or not SUPABASE_KEY:
        if os.environ.get('VERCEL') or os.environ.get('VERCEL_URL'):
            return False, "خطأ: مفاتيح رفع الصور غير موجودة في بيئة تشغيل Vercel."
            
        # Fallback to local storage (للتطوير المحلي فقط)
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
    
    # --- رفع على Supabase Storage ---
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        
        # إنشاء اسم فريد للملف
        temp_filename = f"{int(time.time())}_{secure_filename(filename)}"
        
        # قراءة محتوى الملف
        if hasattr(file_obj, 'read'):
            file_bytes = file_obj.read()
        else:
            file_bytes = file_obj
        
        # تحديد نوع المحتوى
        if filename.lower().endswith('.webp'):
            content_type = 'image/webp'
        elif filename.lower().endswith('.png'):
            content_type = 'image/png'
        elif filename.lower().endswith(('.jpg', '.jpeg')):
            content_type = 'image/jpeg'
        else:
            content_type = 'application/octet-stream'
        
        # رفع الملف مع Cache-Control
        # max-age=31536000 = سنة كاملة (الصور لا تتغير بعد رفعها)
        # هذا يعني إن المتصفح هيحفظ الصورة عنده ومش هيحملها تاني من السيرفر
        # وبالتالي بنوفر الباندويث بشكل كبير جداً
        file_options = {
            "content-type": content_type,
            "cache-control": "public, max-age=31536000, immutable"
        }
        
        res = supabase.storage.from_(SUPABASE_BUCKET).upload(
            temp_filename, 
            file_bytes, 
            file_options=file_options
        )
        
        if getattr(res, 'error', None) and res.error:
             return False, str(res.error)
             
        public_url = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(temp_filename)
        return True, public_url
        
    except Exception as e:
        return False, f"فشل رفع الصورة: {str(e)}"
