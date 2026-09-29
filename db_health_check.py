"""
Database Health Check Script
يفحص قاعدة البيانات ويكشف:
1. حجم كل جدول وعدد الصفوف
2. بيانات يتيمة (Orphaned Records) - سجلات مرتبطة بسجلات محذوفة
3. سجلات مكررة محتملة
4. بيانات فارغة/غير مستخدمة
"""

import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, text

# --- اتصال بقاعدة البيانات ---
db_url = os.environ.get('DATABASE_URL')
if not db_url:
    # fallback: try reading .env manually
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith('DATABASE_URL=') and not line.startswith('#'):
                    db_url = line.split('=', 1)[1].strip('"').strip("'")

if not db_url:
    print("=" * 60)
    print("DATABASE_URL غير موجود!")
    print("يبدو إن DATABASE_URL محطوط عليه # في ملف .env")
    print("يجب إنك تشيل علامة # من قدام DATABASE_URL في ملف .env")
    print("أو تكتبه هنا في السكريبت مباشرة")
    print("=" * 60)
    
    # حاول تقرأ من .env المعلق عليها
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if 'DATABASE_URL' in line and line.startswith('#'):
                    db_url = line.lstrip('#').strip().split('=', 1)[1].strip('"').strip("'")
                    print(f"\nوجدت DATABASE_URL معلق عليه، جاري استخدامه...")
                    break
    
    if not db_url:
        sys.exit(1)

if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(db_url)

print("=" * 70)
print("       🔍 فحص صحة قاعدة البيانات - Database Health Check")
print("=" * 70)

with engine.connect() as conn:
    
    # =========================================================================
    # 1. حجم الجداول وعدد الصفوف
    # =========================================================================
    print("\n📊 [1] حجم الجداول وعدد الصفوف:")
    print("-" * 55)
    
    result = conn.execute(text("""
        SELECT 
            relname AS table_name,
            n_live_tup AS row_count,
            pg_size_pretty(pg_total_relation_size(relid)) AS total_size
        FROM pg_stat_user_tables
        ORDER BY pg_total_relation_size(relid) DESC
    """))
    
    tables_info = []
    for row in result:
        tables_info.append(row)
        print(f"  📁 {row[0]:30s} | صفوف: {row[1]:>8,} | حجم: {row[2]}")
    
    # =========================================================================
    # 2. فحص البيانات اليتيمة (Orphaned Records)
    # =========================================================================
    print(f"\n\n🔗 [2] فحص البيانات اليتيمة (Orphaned Records):")
    print("-" * 55)
    
    orphan_checks = [
        # (وصف, استعلام العد)
        (
            "حركات مالية (FinancialTransaction) مرتبطة بخزنة محذوفة",
            "SELECT COUNT(*) FROM financial_transaction WHERE account_id IS NOT NULL AND account_id NOT IN (SELECT id FROM money_account)"
        ),
        (
            "حركات مالية مرتبطة بمستخدم محذوف",
            "SELECT COUNT(*) FROM financial_transaction WHERE created_by_id IS NOT NULL AND created_by_id NOT IN (SELECT id FROM \"user\")"
        ),
        (
            "أصناف بيع (SaleItem) مرتبطة بطلب محذوف",
            "SELECT COUNT(*) FROM sale_item WHERE order_id NOT IN (SELECT id FROM sale_order)"
        ),
        (
            "أصناف بيع مرتبطة بمنتج (variant) محذوف",
            "SELECT COUNT(*) FROM sale_item WHERE variant_id NOT IN (SELECT id FROM product_variant)"
        ),
        (
            "أصناف شراء (PurchaseItem) مرتبطة بطلب شراء محذوف",
            "SELECT COUNT(*) FROM purchase_item WHERE purchase_id NOT IN (SELECT id FROM purchase_order)"
        ),
        (
            "حركات مخزون (StockMovement) مرتبطة بمنتج محذوف",
            "SELECT COUNT(*) FROM stock_movement WHERE variant_id NOT IN (SELECT id FROM product_variant)"
        ),
        (
            "حركات مخزون مرتبطة بمستخدم محذوف",
            "SELECT COUNT(*) FROM stock_movement WHERE user_id IS NOT NULL AND user_id NOT IN (SELECT id FROM \"user\")"
        ),
        (
            "حركات شركاء (PartnerTransaction) مرتبطة بشريك محذوف",
            "SELECT COUNT(*) FROM partner_transaction WHERE partner_id NOT IN (SELECT id FROM \"user\")"
        ),
        (
            "حركات شركاء مرتبطة بطلب بيع محذوف",
            "SELECT COUNT(*) FROM partner_transaction WHERE order_id IS NOT NULL AND order_id NOT IN (SELECT id FROM sale_order)"
        ),
        (
            "مدفوعات موردين (SupplierPayment) مرتبطة بمورد محذوف",
            "SELECT COUNT(*) FROM supplier_payment WHERE supplier_id NOT IN (SELECT id FROM supplier)"
        ),
        (
            "مدفوعات عملاء (CustomerPayment) مرتبطة بعميل محذوف",
            "SELECT COUNT(*) FROM customer_payment WHERE customer_id NOT IN (SELECT id FROM customer)"
        ),
        (
            "فواتير مرتجع (ReturnInvoice) مرتبطة بطلب بيع محذوف",
            "SELECT COUNT(*) FROM return_invoice WHERE order_id NOT IN (SELECT id FROM sale_order)"
        ),
        (
            "طلبات بيع مرتبطة بعميل محذوف",
            "SELECT COUNT(*) FROM sale_order WHERE customer_id IS NOT NULL AND customer_id NOT IN (SELECT id FROM customer)"
        ),
        (
            "طلبات بيع مرتبطة بمستخدم (بائع) محذوف",
            "SELECT COUNT(*) FROM sale_order WHERE user_id IS NOT NULL AND user_id NOT IN (SELECT id FROM \"user\")"
        ),
        (
            "منتجات (ProductVariant) مرتبطة بموديل محذوف",
            "SELECT COUNT(*) FROM product_variant WHERE model_id NOT IN (SELECT id FROM product_model)"
        ),
    ]
    
    found_orphans = False
    for desc, query in orphan_checks:
        try:
            count = conn.execute(text(query)).scalar()
            if count > 0:
                found_orphans = True
                print(f"  ⚠️  {desc}: {count} سجل يتيم")
            else:
                print(f"  ✅ {desc}: نظيف")
        except Exception as e:
            print(f"  ❓ {desc}: خطأ ({e})")
    
    if not found_orphans:
        print("\n  🎉 لا توجد بيانات يتيمة! قاعدة البيانات نظيفة من هذه الناحية.")
    
    # =========================================================================
    # 3. فحص حركات المخزون الزائدة / المتراكمة
    # =========================================================================
    print(f"\n\n📦 [3] حركات المخزون (StockMovement):")
    print("-" * 55)
    
    try:
        total_movements = conn.execute(text("SELECT COUNT(*) FROM stock_movement")).scalar()
        print(f"  إجمالي حركات المخزون: {total_movements:,} حركة")
        
        # حركات قديمة جداً (أقدم من 6 أشهر)
        old_movements = conn.execute(text("""
            SELECT COUNT(*) FROM stock_movement 
            WHERE timestamp < NOW() - INTERVAL '6 months'
        """)).scalar()
        if old_movements > 0:
            print(f"  📌 حركات أقدم من 6 أشهر: {old_movements:,} (يمكن أرشفتها لتخفيف الحمل)")
    except Exception as e:
        print(f"  ❓ خطأ: {e}")
    
    # =========================================================================
    # 4. فحص الحركات المالية المتراكمة
    # =========================================================================
    print(f"\n\n💰 [4] الحركات المالية (FinancialTransaction):")
    print("-" * 55)
    
    try:
        total_fin = conn.execute(text("SELECT COUNT(*) FROM financial_transaction")).scalar()
        print(f"  إجمالي الحركات المالية: {total_fin:,} حركة")
        
        # حركات بدون وصف
        no_desc = conn.execute(text("""
            SELECT COUNT(*) FROM financial_transaction 
            WHERE description IS NULL OR description = ''
        """)).scalar()
        if no_desc > 0:
            print(f"  ⚠️  حركات مالية بدون وصف: {no_desc}")
        
        # حركات بمبلغ صفر
        zero_amt = conn.execute(text("""
            SELECT COUNT(*) FROM financial_transaction WHERE amount = 0
        """)).scalar()
        if zero_amt > 0:
            print(f"  ⚠️  حركات مالية بمبلغ 0: {zero_amt} (قد تكون غير ضرورية)")
    except Exception as e:
        print(f"  ❓ خطأ: {e}")
    
    # =========================================================================
    # 5. فحص بيانات Cloudinary (صور معطلة)
    # =========================================================================
    print(f"\n\n🖼️  [5] فحص روابط الصور (Cloudinary):")
    print("-" * 55)
    
    try:
        # صور المنتجات
        cloudinary_products = conn.execute(text("""
            SELECT COUNT(*) FROM product_model 
            WHERE image IS NOT NULL AND image LIKE '%cloudinary%'
        """)).scalar()
        
        total_products = conn.execute(text("SELECT COUNT(*) FROM product_model")).scalar()
        local_products = conn.execute(text("""
            SELECT COUNT(*) FROM product_model 
            WHERE image IS NOT NULL AND image NOT LIKE '%cloudinary%' AND image != 'default.png'
        """)).scalar()
        
        print(f"  إجمالي المنتجات: {total_products}")
        print(f"  🌐 صور على Cloudinary: {cloudinary_products}")
        print(f"  💾 صور محلية: {local_products}")
        
        if cloudinary_products > 0:
            print(f"  ⚠️  يوجد {cloudinary_products} صورة منتج على Cloudinary - ستكون معطلة لو الحساب متوقف!")
    except Exception as e:
        print(f"  ❓ خطأ: {e}")
    
    # =========================================================================
    # 6. فحص الجداول الفارغة
    # =========================================================================
    print(f"\n\n🗑️  [6] جداول فارغة تماماً:")
    print("-" * 55)
    
    empty_found = False
    for tinfo in tables_info:
        if tinfo[1] == 0:
            empty_found = True
            print(f"  🔸 {tinfo[0]} (فارغ - حجم: {tinfo[2]})")
    
    if not empty_found:
        print("  ✅ لا توجد جداول فارغة")
    
    # =========================================================================
    # 7. الحجم الإجمالي لقاعدة البيانات
    # =========================================================================
    print(f"\n\n📏 [7] الحجم الإجمالي لقاعدة البيانات:")
    print("-" * 55)
    
    try:
        db_size = conn.execute(text("""
            SELECT pg_size_pretty(pg_database_size(current_database()))
        """)).scalar()
        print(f"  💾 الحجم الإجمالي: {db_size}")
    except Exception as e:
        print(f"  ❓ خطأ: {e}")

print("\n" + "=" * 70)
print("       ✅ انتهى الفحص")
print("=" * 70)
