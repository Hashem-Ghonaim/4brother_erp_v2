"""
Routes: inventory
Auto-extracted from backend/app.py during refactoring.
"""
import os
import json
import math
import re
import random
import string
from datetime import datetime, date, timedelta
from functools import wraps
from flask import render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy import func, text, or_

from ..core import app, db, cairo_now, basedir, FACTORY_LAT, FACTORY_LNG, ALLOWED_RADIUS, allowed_file, BASE_DIR
from ..models import (
    SystemSetting, AttendanceSettings, User, Attendance, PatternTracking,
    Category, ProductModel, EmployeeExcuse, ProductVariant, Supplier,
    PartnerTransaction, SupplierPayment, PurchaseOrder, PurchaseItem,
    Customer, CustomerPayment, ShippingCompany, SaleOrder, SaleItem,
    FinancialTransaction, ReturnInvoice, MoneyAccount, StockMovement,
    HRTransaction, ExpenseCategory, Expense
)
from ..helpers import (general_manager_required, permission_required, permission_required_any,
                       get_accessible_users, get_allowed_customers,
                       calculate_user_commission, calculate_distance)


@app.route('/public/catalog')
def public_catalog():
    ref = request.args.get('ref')
    if not ref:
        return "عفواً، لا يمكنك الوصول إلى هذا الرابط مباشرة. يرجى الحصول على رابط دعوة من أحد ممثلي المبيعات الخاص بنا.", 403

    # جلب كل التصنيفات
    all_cats = Category.query.all()
    catalog_data = []

    for cat in all_cats:
        # جلب المنتجات المتاحة فقط (رصيد > 0) التابعة لهذا التصنيف
        # نستخدم join لأن الـ category_id موجود في ProductModel وليس Variant
        products = ProductVariant.query.join(ProductModel).filter(
            ProductModel.category_id == cat.id,
            ProductVariant.stock > 0
        ).all()

        # إذا كان التصنيف يحتوي على منتجات متاحة، نضيفه للقائمة
        if products:
            catalog_data.append({
                'category': cat,
                'products': products
            })

    return render_template('public_catalog.html',
                           catalog_data=catalog_data,
                           company_name= "مصنع فور برازر")

@app.route('/api/update_product_image', methods=['POST'])
@permission_required('manage_inventory')
def update_product_image():
    try:
        product_id = request.form.get('id')
        if 'image' not in request.files:
            return jsonify({'success': False, 'message': 'لم يتم اختيار صورة'}), 400
        
        file = request.files['image']
        if file.filename == '':
            return jsonify({'success': False, 'message': 'اسم الملف فارغ'}), 400

        if file:
            filename = secure_filename(file.filename)
            filename = f"{int(cairo_now().timestamp())}_{filename}"
            from backend.supabase_storage import upload_file_to_supabase
            success, url = upload_file_to_supabase(file, filename, app.config)
            
            if success:
                # تحديث المنتج
                variant = ProductVariant.query.get(product_id)
                if variant:
                    variant.model.image = url
                    db.session.commit()
                    return jsonify({'success': True, 'message': 'تم تحديث الصورة بنجاح', 'image_url': url})
                else:
                    return jsonify({'success': False, 'message': 'المنتج غير موجود'}), 404
            else:
                return jsonify({'success': False, 'message': f'فشل رفع الصورة: {url}'}), 500

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/inventory')
@permission_required_any('manage_inventory', 'print_barcode', 'view_inventory')
def inventory(): return render_template('inventory.html', products=ProductVariant.query.join(ProductModel).filter(ProductModel.season == session.get('active_season', 'شتوي 2027')).order_by(ProductVariant.id).all(), user=current_user, categories=Category.query.all())


@app.route('/verify_password_api', methods=['POST'])
@login_required
def verify_password_api():
    data = request.get_json()
    password = data.get('password', '')
    if check_password_hash(current_user.password, password):
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': 'كلمة السر غلط'}), 401


@app.route('/product/edit/<int:id>', methods=['POST'])
@permission_required('manage_inventory')
def edit_product(id):
    var = ProductVariant.query.get_or_404(id)
    var.model.name = request.form['name']; var.cost_price = float(request.form['cost']); var.sell_price = float(request.form['sell'])
    if 'season' in request.form: var.model.season = request.form['season']
    new_stock = int(request.form['stock']); diff = new_stock - var.stock
    # تحديث الصورة لو موجودة
    if 'image' in request.files:
        file = request.files['image']
        if file and file.filename != '':
            filename = secure_filename(file.filename)
            from backend.supabase_storage import upload_file_to_supabase
            success, url = upload_file_to_supabase(file, filename, app.config)
            if success:
                var.model.image = url
    if diff != 0: var.stock = new_stock; db.session.add(StockMovement(variant_id=var.id, user_id=current_user.id, quantity_change=diff, reason="تعديل يدوي"))
    db.session.commit(); return redirect(url_for('inventory'))


@app.route('/product/tracking')
@login_required
@permission_required('view_inventory')
def product_tracking():
    variant_id = request.args.get('variant_id', type=int)
    exact_variant_id = request.args.get('exact_variant_id', type=int)
    
    variant = None
    history = []
    total_in = 0
    total_out = 0
    
    # نجيب كل الأصناف عشان الـ dropdown في البحث
    all_variants = ProductVariant.query.join(ProductModel).join(Category).order_by(Category.name).all()

    # لو بيبحث برقم الصنف (ID) المطابق أولاً
    if exact_variant_id:
        variant = ProductVariant.query.get(exact_variant_id)
        if not variant:
            flash('رقم الصنف (ID) غير موجود في النظام!', 'warning')
    # لو اختار من القائمة
    elif variant_id:
        variant = ProductVariant.query.get_or_404(variant_id)
        
    if variant:
        # جلب الحركات كلها للصنف ده مترتبة من الأقدم للأحدث عشان نحسب الرصيد التراكمي
        movements = StockMovement.query.filter_by(variant_id=variant.id).order_by(StockMovement.timestamp.asc(), StockMovement.id.asc()).all()
        
        running_balance = 0
        for mov in movements:
            running_balance += mov.quantity_change
            
            if mov.quantity_change > 0:
                total_in += mov.quantity_change
            elif mov.quantity_change < 0:
                total_out += abs(mov.quantity_change)
                
            # استخراج رابط الفاتورة من الوصف (لو موجود)
            url = None
            reason = mov.reason

            
            # بيع فاتورة #id
            sale_match = re.search(r'بيع فاتورة #(\d+)', reason)
            if sale_match:
                url = url_for('print_invoice', id=int(sale_match.group(1)))
                
            # شراء فاتورة #id
            purchase_match = re.search(r'فاتورة شراء #(\d+)', reason)
            if purchase_match:
                url = url_for('purchase_details', id=int(purchase_match.group(1)))
                
            # مرتجع فاتورة #id
            return_match = re.search(r'مرتجع فاتورة #(\d+)', reason)
            if return_match:
                url = url_for('returns_list') # أو رابط تفاصيل المرتجع لو موجود
                
            history.insert(0, {
                'id': mov.id,
                'date': mov.timestamp,
                'reason': mov.reason,
                'user': User.query.get(mov.user_id).fullname if mov.user_id else 'نظام',
                'change': mov.quantity_change,
                'balance_after': running_balance,
                'url': url
            })
            
    return render_template('product_tracking.html', variant=variant, history=history, all_variants=all_variants, total_in=total_in, total_out=total_out, exact_variant_id=exact_variant_id)

@app.route('/product/delete/<int:id>')
@permission_required('manage_inventory')
def delete_product(id):
    try: var = ProductVariant.query.get_or_404(id); db.session.delete(var.model); db.session.delete(var); db.session.commit()
    except: pass
    return redirect(url_for('inventory'))


@app.route('/print_barcode/<int:id>')
@login_required
@permission_required_any('manage_inventory', 'print_barcode')
def print_barcode(id): return render_template('print_barcode.html', product=ProductVariant.query.get_or_404(id))


@app.route('/api/quick_update_product', methods=['POST'])
@permission_required('manage_inventory')
def quick_update_product():
    data = request.get_json()
    product_id = data.get('id')
    field = data.get('field') # name, cost_price, sell_price, stock
    value = data.get('value')

    variant = ProductVariant.query.get(product_id)
    if not variant:
        return jsonify({'success': False, 'message': 'المنتج غير موجود'}), 404

    try:
        if field == 'name':
            variant.model.name = value
        elif field == 'season':
            variant.model.season = value
        elif field == 'cost':
            variant.cost_price = float(value)
        elif field == 'sell':
            variant.sell_price = float(value)
        elif field == 'stock':
            old_stock = variant.stock
            new_stock = int(value)
            diff = new_stock - old_stock
            if diff != 0:
                variant.stock = new_stock
                # تسجيل حركة مخزون للتعديل اليدوي
                db.session.add(StockMovement(
                    variant_id=variant.id,
                    user_id=current_user.id,
                    quantity_change=diff,
                    reason="تعديل سريع من الجدول"
                ))

        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/inventory/print_catalog')
@login_required  # متاح لأي شخص مسجل دخول
def print_inventory_catalog():
    # استقبال التصنيف من الرابط
    cat_id = request.args.get('category_id')

    # الاستعلام الأساسي: ترتيب بالكود + (شرط المخزون أكبر من صفر)
    query = ProductVariant.query.filter(ProductVariant.stock > 0).join(ProductModel).order_by(ProductVariant.id)

    title_text = "كل المنتجات المتوفرة"

    # تطبيق فلتر التصنيف
    if cat_id and cat_id != 'all':
        query = query.filter(ProductModel.category_id == cat_id)
        category = Category.query.get(cat_id)
        if category:
            title_text = category.name

    products = query.all()

    return render_template('print_catalog.html', products=products, catalog_title=title_text)

@app.route('/api/search_product')
def search_product():
    q = request.args.get('q', '')
    # بنجيب المنتج مع التصنيف بتاعه عشان نعرضه
    products = ProductModel.query.join(Category).filter(ProductModel.name.ilike(f'%{q}%')).limit(20).all()
    results = []
    for p in products:
        results.append({
            'id': p.id,
            'name': p.name,       # الاسم الصافي
            'category_id': p.category_id,
            'category_name': p.category.name,
            # دا اللي هيظهرلك في القائمة: "اسم المنتج (اسم التصنيف)"
            'label': f"{p.name} - ({p.category.name})"
        })
    return jsonify(results)
