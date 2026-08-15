"""
Routes: patterns
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
from flask import render_template, request, redirect, url_for, flash, jsonify
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


@app.route('/patterns')
@login_required
@permission_required('view_patterns')
def pattern_tracking():
    # جلب الفلاتر من الرابط
    status_filter = request.args.get('status', '')
    factory_filter = request.args.get('factory', '')
    customer_filter = request.args.get('customer_id', '')
    responsible_filter = request.args.get('responsible_id', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    customer_name_filter = request.args.get('customer_name', '')

    query = PatternTracking.query

    # تطبيق الفلاتر
    if status_filter:
        query = query.filter(PatternTracking.status == status_filter)
    if factory_filter:
        query = query.filter(PatternTracking.factory_name.like(f"%{factory_filter}%"))
    if customer_filter:
        query = query.filter(PatternTracking.customer_id == customer_filter)
    
    # فلتر اسم العميل النصي
    if customer_name_filter:
        query = query.join(Customer).filter(Customer.name.like(f"%{customer_name_filter}%"))
        
    if responsible_filter:
        query = query.filter(PatternTracking.responsible_id == responsible_filter)
    if start_date:
        query = query.filter(PatternTracking.entry_date >= datetime.strptime(start_date, '%Y-%m-%d').date())
    if end_date:
        query = query.filter(PatternTracking.entry_date <= datetime.strptime(end_date, '%Y-%m-%d').date())

    # عزل العملاء بالنسبة للموظف العادي (المشرفين أو من لديهم manage_patterns يرون الكل)
    if current_user.role != 'general_manager' and not current_user.has_perm('manage_patterns'):
        # إذا لم يكن هناك فلتر اسم للعميل، نطبق join إذا لزم الأمر
        if not customer_name_filter:
            query = query.join(Customer)
        query = query.filter(Customer.created_by_id == current_user.id)
    
    # ترتيب تنازلي (الأحدث أولاً)
    patterns = query.order_by(PatternTracking.id.desc()).all()

    # تجهيز قوائم الفلاتر للإضافة وللبحث
    # إظهار كافة العملاء في القائمة الخاصة بالبحث والإضافة
    customers = Customer.query.order_by(Customer.name).all()
    
    # 2. المسؤولين (كل الموظفين)
    users = User.query.order_by(User.fullname).all()

    return render_template('pattern_tracking.html', 
                           patterns=patterns, 
                           customers=customers, 
                           users=users,
                           status_filter=status_filter,
                           factory_filter=factory_filter,
                           customer_filter=customer_filter,
                           customer_name_filter=customer_name_filter,
                           responsible_filter=responsible_filter,
                           start_date=start_date,
                           end_date=end_date)



@app.route('/patterns/new', methods=['POST'])
@login_required
@permission_required('manage_patterns')
def new_pattern():
    serial_number = request.form.get('serial_number')
    entry_date = request.form.get('entry_date')
    delivery_date = request.form.get('delivery_date')
    factory_name = request.form.get('factory_name')
    customer_id = request.form.get('customer_id')
    responsible_id = request.form.get('responsible_id')
    cost = request.form.get('cost', 0.0)
    receiving_price = request.form.get('receiving_price', 0.0)
    selling_price = request.form.get('selling_price', 0.0)
    status = request.form.get('status', 'جاري التجهيز')
    season = request.form.get('season', 'صيفي 2026')
    
    # رفع الصورة (اختياري)
    filename = None
    if 'image' in request.files:
        file = request.files['image']
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            unique_filename = f"pattern_{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
            from backend.supabase_storage import upload_file_to_supabase
            success, url = upload_file_to_supabase(file, unique_filename, app.config)
            if success:
                filename = url

    new_pat = PatternTracking(
        serial_number=serial_number,
        image=filename,
        entry_date=datetime.strptime(entry_date, '%Y-%m-%d').date() if entry_date else date.today(),
        delivery_date=datetime.strptime(delivery_date, '%Y-%m-%d').date() if delivery_date else None,
        factory_name=factory_name,
        customer_id=customer_id,
        responsible_id=responsible_id,
        cost=float(cost) if cost else 0.0,
        receiving_price=float(receiving_price) if receiving_price else 0.0,
        selling_price=float(selling_price) if selling_price else 0.0,
        quantity=int(request.form.get('quantity', 0) or 0),
        colors=request.form.get('colors', ''),
        status=status,
        season=season
    )

    db.session.add(new_pat)
    db.session.commit()
    flash('تم إضافة القصة بنجاح', 'success')
    return redirect(url_for('pattern_tracking'))



@app.route('/patterns/edit/<int:id>', methods=['POST'])
@login_required
@permission_required('manage_patterns')
def edit_pattern(id):
    pat = PatternTracking.query.get_or_404(id)
    
    pat.serial_number = request.form.get('serial_number')
    
    # تواريخ
    entry_val = request.form.get('entry_date')
    if entry_val: pat.entry_date = datetime.strptime(entry_val, '%Y-%m-%d').date()
    
    delivery_val = request.form.get('delivery_date')
    if delivery_val: pat.delivery_date = datetime.strptime(delivery_val, '%Y-%m-%d').date()
    else: pat.delivery_date = None

    pat.factory_name = request.form.get('factory_name')
    pat.customer_id = request.form.get('customer_id')
    pat.responsible_id = request.form.get('responsible_id')
    
    cost_val = request.form.get('cost')
    if cost_val: pat.cost = float(cost_val)

    rec_price_val = request.form.get('receiving_price')
    if rec_price_val is not None: pat.receiving_price = float(rec_price_val) if rec_price_val else 0.0

    selling_price_val = request.form.get('selling_price')
    if selling_price_val: pat.selling_price = float(selling_price_val)

    pat.status = request.form.get('status')
    
    qty_val = request.form.get('quantity')
    pat.quantity = int(qty_val) if qty_val else 0
    pat.colors = request.form.get('colors', '')
    
    season_val = request.form.get('season')
    if season_val: pat.season = season_val
    
    # تحديث الصورة
    if 'image' in request.files:
        file = request.files['image']
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            unique_filename = f"pattern_{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
            from backend.supabase_storage import upload_file_to_supabase
            success, url = upload_file_to_supabase(file, unique_filename, app.config)
            if success:
                pat.image = url

    db.session.commit()
    flash('تم حفظ التعديلات', 'success')
    return redirect(url_for('pattern_tracking'))



@app.route('/patterns/delete/<int:id>')
@login_required
@permission_required('manage_patterns')
def delete_pattern(id):
    pat = PatternTracking.query.get_or_404(id)
    db.session.delete(pat)
    db.session.commit()
    flash('تم حذف القصة', 'success')
    return redirect(url_for('pattern_tracking'))

