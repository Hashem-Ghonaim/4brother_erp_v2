"""
Routes: customers
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


@app.route('/customers/pay', methods=['POST'])
@login_required
def add_customer_payment():
    try:
        cid = request.form.get('customer_id')
        amount = float(request.form.get('amount') or 0)
        acc_id = request.form.get('account_id')
        notes = request.form.get('notes', '')

        customer = Customer.query.get_or_404(cid)
        account = MoneyAccount.query.get_or_404(acc_id)

        if amount <= 0:
            flash('يجب إدخال مبلغ أكبر من الصفر', 'warning')
            return redirect(url_for('customer_profile', id=cid))

        # 1. خصم المبلغ من مديونية العميل
        customer.balance = (customer.balance or 0) - amount

        # 2. زيادة رصيد الخزينة المختارة
        account.balance = (account.balance or 0) + amount

        # 3. تسجيل دفعة العميل في جدول المدفوعات
        # تأكد أنك أنشأت موديل CustomerPayment كما شرحنا سابقاً
        payment = CustomerPayment(
            customer_id=cid,
            amount=amount,
            account_id=acc_id,
            notes=notes
        )
        db.session.add(payment)

        # 4. تسجيل حركة مالية (إيراد) في سجلات الخزينة العامة
        db.session.add(FinancialTransaction(
            account_id=acc_id,
            type='income',
            category='تحصيل مديونية',
            amount=amount,
            description=f"تحصيل من حساب العميل: {customer.name} ({notes})",
            created_by_id=current_user.id,
            date=cairo_now()
        ))

        db.session.commit()
        flash(f'تم تحصيل {amount} ج.م وإضافتها لـ {account.name} بنجاح ✅', 'success')
        return redirect(url_for('customer_profile', id=cid))
    except Exception as e:
        db.session.rollback()
        flash(f'حدث خطأ أثناء العملية: {str(e)}', 'danger')
        return redirect(url_for('customer_profile', id=cid))

@app.route('/customers')
@login_required
def customers():
    if not current_user.has_perm('view_customers'):
        return "غير مصرح لك", 403

    # جلب قائمة المعرفات المسموح بها
    accessible_ids = get_accessible_users()

    # فلترة العملاء: عرض العميل فقط إذا كان منشئه (created_by) ضمن القائمة المسموحة
    # أو إذا كان العميل "عام" (created_by_id = NULL) لو حابب تسمح بده،
    # لكن الكود هنا سيجبر الرؤية بناءً على المنشئ.
    all_customers = Customer.query.filter(Customer.created_by_id.in_(accessible_ids)).order_by(Customer.id.desc()).all()

    return render_template('customers.html', customers=all_customers)

@app.route('/customers/add', methods=['POST'])
@login_required
def add_customer():
    name = request.form.get('name')
    phone = request.form.get('phone')
    address = request.form.get('address')

    # التحقق من أن جميع الحقول ممتلئة وليست فارغة
    if not name or not name.strip() or not phone or not phone.strip() or not address or not address.strip():
        flash('❌ خطأ: جميع بيانات العميل (الاسم، الهاتف، والعنوان) مطلوبة.', 'danger')
        return redirect(request.referrer)

    # التحقق من عدم تكرار رقم الهاتف
    if Customer.query.filter_by(phone=phone).first():
        flash('❌ خطأ: رقم الهاتف مسجل لعميل آخر بالفعل.', 'warning')
        return redirect(request.referrer)

    db.session.add(Customer(
        name=name,
        phone=phone,
        address=address,
        created_by_id=current_user.id
    ))
    db.session.commit()

    flash('✅ تم إضافة العميل الجديد بنجاح.', 'success')
    return redirect(request.referrer)

@app.route('/customers/<int:id>')
@login_required
def customer_profile(id):
    customer = Customer.query.get_or_404(id)
    # جلب جميع فواتير العميل
    orders = SaleOrder.query.filter_by(customer_id=id).order_by(SaleOrder.date.desc()).all()
    # جلب جميع الخزائن لعرضها في القائمة المنسدلة
    accounts = MoneyAccount.query.all()
    # جلب سجل المدفوعات التي دفعها العميل (عبر الـ backref المسمى payments_received)
    payments = customer.payments_received if hasattr(customer, 'payments_received') else []

    return render_template('customer_profile.html',
                           customer=customer,
                           orders=orders,
                           accounts=accounts,
                           payments=payments)# --- إدارة المصروفات الشاملة ---
# استبدل دالة expenses و add_expense بهذا الكود الموحد
# أضف هذا الرابط الجديد للتعامل مع تحديث الصورة السريع

@app.route('/api/customers/add', methods=['POST'])
@login_required
def api_add_customer():
    try:
        data = request.get_json()
        name = data.get('name')
        phone = data.get('phone')
        address = data.get('address')

        if not name or not phone:
             return jsonify({'success': False, 'message': 'الاسم والهاتف مطلوبان'}), 400
        
        if Customer.query.filter_by(phone=phone).first():
            return jsonify({'success': False, 'message': 'رقم الهاتف مسجل مسبقاً'}), 400

        new_customer = Customer(
            name=name,
            phone=phone,
            address=address,
            created_by_id=current_user.id
        )
        db.session.add(new_customer)
        db.session.commit()

        return jsonify({
            'success': True, 
            'message': 'تم إضافة العميل بنجاح',
            'customer': {
                'id': new_customer.id,
                'name': new_customer.name,
                'phone': new_customer.phone
            }
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/customer/edit/<int:id>', methods=['POST'])
@login_required
def edit_customer(id):
    # التحقق من الصلاحية
    if not current_user.has_perm('manage_customers') and current_user.role != 'general_manager':
        flash('غير مصرح لك بتعديل البيانات', 'danger')
        return redirect(url_for('customers'))

    cust = Customer.query.get_or_404(id)

    # تحديث البيانات
    cust.name = request.form.get('name')
    cust.phone = request.form.get('phone')
    cust.address = request.form.get('address')

    db.session.commit()
    flash(f'تم تحديث بيانات العميل {cust.name} بنجاح ✅', 'success')
    return redirect(url_for('customers'))
