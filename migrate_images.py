"""
سكريبت محاولة سحب الصور من Cloudinary المتوقف ورفعها على Supabase Storage.
الخطة:
1. نجيب كل روابط Cloudinary من قاعدة البيانات
2. نحاول نحمل كل صورة من الرابط
3. نرفعها على Supabase Storage
4. نحدّث الرابط في قاعدة البيانات
"""

import os
import sys
import time
import requests

sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, text

# --- إعداد قاعدة البيانات ---
db_url = None
env_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if 'DATABASE_URL' in line and line.startswith('#'):
                db_url = line.lstrip('#').strip().split('=', 1)[1].strip('"').strip("'")
                break

if not db_url:
    db_url = os.environ.get('DATABASE_URL')

if not db_url:
    print("ERROR: DATABASE_URL not found!")
    sys.exit(1)

if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(db_url)

# --- إعداد Supabase ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "images")

print("=" * 70)
print("  Cloudinary -> Supabase Storage Migration Tool")
print("=" * 70)

# === الخطوة 1: جلب كل روابط Cloudinary من قاعدة البيانات ===
print("\n[1/4] Fetching Cloudinary URLs from database...")

with engine.connect() as conn:
    # صور المنتجات
    product_images = conn.execute(text("""
        SELECT id, image FROM product_model 
        WHERE image IS NOT NULL AND image LIKE '%cloudinary%'
    """)).fetchall()
    
    # صور الباترنات
    pattern_images = conn.execute(text("""
        SELECT id, image FROM pattern_tracking 
        WHERE image IS NOT NULL AND image LIKE '%cloudinary%'
    """)).fetchall()
    
    # صور إيصالات الموردين
    receipt_images = conn.execute(text("""
        SELECT id, receipt_image FROM supplier_payment 
        WHERE receipt_image IS NOT NULL AND receipt_image LIKE '%cloudinary%'
    """)).fetchall()

total = len(product_images) + len(pattern_images) + len(receipt_images)
print(f"  - Product images (product_model): {len(product_images)}")
print(f"  - Pattern images (pattern_tracking): {len(pattern_images)}")
print(f"  - Receipt images (supplier_payment): {len(receipt_images)}")
print(f"  - TOTAL: {total} images to migrate")

if total == 0:
    print("\nNo Cloudinary images found. Nothing to migrate!")
    sys.exit(0)

# === الخطوة 2: اختبار الوصول لصورة واحدة ===
print("\n[2/4] Testing Cloudinary access...")

test_url = None
if product_images:
    test_url = product_images[0][1]
elif pattern_images:
    test_url = pattern_images[0][1]
elif receipt_images:
    test_url = receipt_images[0][1]

if test_url:
    try:
        resp = requests.get(test_url, timeout=10)
        if resp.status_code == 200:
            print(f"  SUCCESS! Cloudinary images are still accessible!")
            print(f"  Test image size: {len(resp.content) / 1024:.1f} KB")
            can_download = True
        else:
            print(f"  FAILED! Status code: {resp.status_code}")
            print(f"  Response: {resp.text[:200]}")
            can_download = False
    except Exception as e:
        print(f"  FAILED! Error: {e}")
        can_download = False
else:
    print("  No test URL available")
    can_download = False

if not can_download:
    print("\n" + "=" * 70)
    print("  Cloudinary account is fully disabled - cannot download images.")
    print("  ")
    print("  Options:")
    print("  1. Re-upload product images manually from your phone/computer")
    print("  2. Try to recover Cloudinary account access")
    print("  3. Set default placeholder images for products without photos")
    print("=" * 70)
    sys.exit(1)

# === الخطوة 3: تحميل الصور من Cloudinary ورفعها على Supabase ===
print(f"\n[3/4] Migrating images to Supabase Storage...")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("  ERROR: SUPABASE_URL or SUPABASE_KEY not set!")
    sys.exit(1)

from supabase import create_client
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

success_count = 0
fail_count = 0
updates = []  # (table, column, id, new_url)

def migrate_image(img_id, img_url, table_name, column_name):
    global success_count, fail_count
    try:
        # تحميل الصورة من Cloudinary
        resp = requests.get(img_url, timeout=30)
        if resp.status_code != 200:
            print(f"  SKIP [{table_name} #{img_id}]: HTTP {resp.status_code}")
            fail_count += 1
            return
        
        img_bytes = resp.content
        
        # استخراج اسم الملف من الرابط
        original_name = img_url.split('/')[-1].split('?')[0]
        new_filename = f"migrated_{int(time.time())}_{img_id}_{original_name}"
        
        # تحديد نوع المحتوى
        if new_filename.lower().endswith('.webp'):
            content_type = 'image/webp'
        elif new_filename.lower().endswith('.png'):
            content_type = 'image/png'
        elif new_filename.lower().endswith(('.jpg', '.jpeg')):
            content_type = 'image/jpeg'
        else:
            content_type = 'image/webp'
            if not new_filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                new_filename += '.webp'
        
        # رفع على Supabase Storage
        file_options = {
            "content-type": content_type,
            "cache-control": "public, max-age=31536000, immutable"
        }
        
        res = supabase.storage.from_(SUPABASE_BUCKET).upload(
            new_filename, img_bytes, file_options=file_options
        )
        
        if getattr(res, 'error', None) and res.error:
            print(f"  FAIL [{table_name} #{img_id}]: {res.error}")
            fail_count += 1
            return
        
        # جلب الرابط العام
        new_url = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(new_filename)
        
        updates.append((table_name, column_name, img_id, new_url))
        success_count += 1
        
        if success_count % 10 == 0:
            print(f"  ... migrated {success_count}/{total} images")
        
        # تأخير بسيط عشان ما نحملش على السيرفر
        time.sleep(0.3)
        
    except Exception as e:
        print(f"  FAIL [{table_name} #{img_id}]: {e}")
        fail_count += 1

# --- نقل صور المنتجات ---
print(f"\n  Migrating {len(product_images)} product images...")
for img_id, img_url in product_images:
    migrate_image(img_id, img_url, 'product_model', 'image')

# --- نقل صور الباترنات ---
print(f"\n  Migrating {len(pattern_images)} pattern images...")
for img_id, img_url in pattern_images:
    migrate_image(img_id, img_url, 'pattern_tracking', 'image')

# --- نقل صور الإيصالات ---
print(f"\n  Migrating {len(receipt_images)} receipt images...")
for img_id, img_url in receipt_images:
    migrate_image(img_id, img_url, 'supplier_payment', 'receipt_image')

# === الخطوة 4: تحديث قاعدة البيانات بالروابط الجديدة ===
print(f"\n[4/4] Updating database with new Supabase URLs...")

with engine.connect() as conn:
    for table, column, row_id, new_url in updates:
        conn.execute(
            text(f'UPDATE {table} SET {column} = :url WHERE id = :id'),
            {'url': new_url, 'id': row_id}
        )
    conn.commit()

print(f"\n{'=' * 70}")
print(f"  Migration Complete!")
print(f"  Successful: {success_count}")
print(f"  Failed:     {fail_count}")
print(f"  DB Updated: {len(updates)} records")
print(f"{'=' * 70}")
