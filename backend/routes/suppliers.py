"""
Routes: suppliers
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


@app.route('/suppliers')
@permission_required('manage_inventory')
def suppliers():
    all_suppliers = Supplier.query.all()

    # حساب إجمالي المديونية (أي رصيد موجب يعتبر فلوس للمورد)
    total_debt = sum(s.balance for s in all_suppliers if s.balance > 0)

    return render_template('suppliers.html', suppliers=all_suppliers, total_debt=total_debt)

@app.route('/suppliers/add', methods=['POST'])
@permission_required('manage_inventory')
def add_supplier():
    name = request.form.get('name')
    phone = request.form.get('phone')
    if name:
        db.session.add(Supplier(name=name, phone=phone))
        db.session.commit()
        flash('تم إضافة المورد بنجاح ✅', 'success')
    else: flash('الاسم مطلوب', 'warning')
    return redirect(url_for('suppliers'))


@app.route('/supplier/edit/<int:id>', methods=['POST'])
@permission_required('manage_inventory')
def edit_supplier(id):
    supp = Supplier.query.get_or_404(id)
    supp.name = request.form.get('name')
    supp.phone = request.form.get('phone')
    db.session.commit()
    flash('تم التحديث', 'success')
    return redirect(url_for('suppliers'))


@app.route('/suppliers/<int:id>')
@permission_required('manage_inventory')
def supplier_profile(id):
    from flask import session
    current_season = session.get('season', 'شتوي 2027')
    
    supp = Supplier.query.get_or_404(id)
    accounts = MoneyAccount.query.all()

    # === الإضافة الجديدة: حساب إجمالي عدد القطع للموسم الحالي ===
    total_items = db.session.query(func.sum(PurchaseItem.quantity))\
        .join(PurchaseOrder)\
        .filter(PurchaseOrder.supplier_id == id, PurchaseOrder.season == current_season)\
        .scalar() or 0

    return render_template('supplier_profile.html',
                           supplier=supp,
                           orders=sorted(supp.orders, key=lambda o: o.id, reverse=True),
                           payments=sorted(supp.payments, key=lambda p: p.id, reverse=True),
                           accounts=accounts,
                           total_items=int(total_items))

@app.route('/suppliers/pay', methods=['POST'])
@permission_required('manage_inventory')
def add_supplier_payment():
    sid = request.form.get('supplier_id')
    try:
        amount = float(request.form.get('amount') or 0)
    except:
        amount = 0

    acc_id = request.form.get('account_id') # استقبال رقم الخزينة

    if amount <= 0:
        flash('يجب إدخال مبلغ صحيح', 'warning')
        return redirect(url_for('supplier_profile', id=sid))

    # التحقق من الخزينة
    account = MoneyAccount.query.get(acc_id)
    if not account:
        flash('يجب اختيار خزينة للسداد منها', 'danger')
        return redirect(url_for('supplier_profile', id=sid))

    # معالجة الصورة
    fname = None
    if 'receipt_image' in request.files and request.files['receipt_image'].filename:
        fname = f"{int(cairo_now().timestamp())}_{secure_filename(request.files['receipt_image'].filename)}"
        from backend.supabase_storage import upload_file_to_supabase
        success, url = upload_file_to_supabase(request.files['receipt_image'], fname, app.config)
        if success:
            fname = url

    # 1. تسجيل عملية السداد للمورد
    payment = SupplierPayment(
        supplier_id=sid,
        amount=amount,
        receipt_image=fname,
        notes=request.form.get('notes'),
        account_id=account.id # ربطها بالخزنة
    )
    db.session.add(payment)

    # 2. تقليل مديونية المورد
    supplier = Supplier.query.get(sid)
    supplier.balance -= amount

    # 3. خصم المبلغ من الخزينة المختارة وتسجيل حركة مالية
    account.balance = round(account.balance - amount, 1)

    db.session.add(FinancialTransaction(
        type='expense', # مصروف
        category='سداد موردين',
        amount=-amount, # بالسالب
        description=f"سداد دفعة للمورد ({supplier.name})",
        date=cairo_now(),
        created_by_id=current_user.id,
        account_id=account.id
    ))

    db.session.commit()
    flash(f'تم تسجيل السداد وخصم {amount} من {account.name} ✅', 'success')
    return redirect(url_for('supplier_profile', id=sid))

@app.route('/suppliers/<int:id>/edit_balance', methods=['POST'])
@permission_required('manage_inventory')
def edit_supplier_balance(id):
    supp = Supplier.query.get_or_404(id)
    new_balance = float(request.form.get('new_balance', 0))
    supp.balance = new_balance
    db.session.commit()
    flash(f'تم تعديل رصيد المورد {supp.name} بنجاح إلى {new_balance}', 'success')
    return redirect(url_for('supplier_profile', id=id))

# === تقارير تفصيلية للمدفوعات الموردين ===
