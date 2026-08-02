from sqlalchemy import cast, Date
"""
Routes: shipping
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


@app.route('/shipping/orders')
@login_required
def shipping_dashboard():
    # التحقق من صلاحية الرؤية (يجب أن تملك EMP201 هذه الصلاحية أو نجعلها استثناء)
    if not current_user.has_perm('view_shipping') and current_user.emp_code != 'EMP201':
        flash('غير مصرح لك', 'danger')
        return redirect(url_for('dashboard'))

    # جلب قائمة المستخدمين المسموح برؤية أوردراتهم
    accessible_ids = get_accessible_users()

    # تعديل الاستعلام لإضافة فلتر user_id
    orders = SaleOrder.query.filter(
        SaleOrder.is_shipping == True,
        SaleOrder.user_id.in_(accessible_ids), # <--- الفلترة الجديدة هنا
        SaleOrder.shipping_status.in_(['none', 'shipped', 'delivered', 'partial_return']),
        db.or_(SaleOrder.shipping_status != 'partial_return', SaleOrder.amount_due > 0)
    ).order_by(SaleOrder.date.desc()).all()

    total_pending = sum(o.amount_due for o in orders if o.shipping_status in ['none', 'shipped', 'delivered', 'partial_return'])

    return render_template('shipping_dashboard.html',
                         orders=orders,
                         total_pending=total_pending,
                         companies=ShippingCompany.query.all(),
                         accounts=MoneyAccount.query.all())

# 2. نعدل دالة التحديث عشان تسجل الفلوس

@app.route('/shipping/update/<int:id>', methods=['POST'])
@login_required
def update_shipping(id):
    # السماح بالدخول لو يملك الصلاحية أو لو كان كوده EMP201
    if not current_user.has_perm('manage_shipping') and current_user.emp_code != 'EMP201':
        flash('غير مصرح لك بإدارة الشحن', 'warning')
        return redirect(request.referrer)

    order = SaleOrder.query.get_or_404(id)
    action = request.form.get('action')

    # ... (باقي الحالات: save_note, set_waybill, edit_waybill, mark_delivered زي ما هي بدون تغيير) ...
    if action == 'save_note':
        order.shipping_notes = request.form.get('note')
        flash('تم حفظ الملاحظة بنجاح 📝', 'success')

    elif action == 'set_waybill':
        order.waybill_no = request.form.get('waybill_no')
        order.shipping_status = 'shipped'
        flash('تم تسجيل خروج الشحنة ✅', 'success')

    elif action == 'edit_waybill':
        new_waybill = request.form.get('waybill_no')
        if new_waybill:
            order.waybill_no = new_waybill
            flash('تم تصحيح رقم البوليصة بنجاح ✏️', 'success')

    elif action == 'mark_delivered':
        order.shipping_status = 'delivered'
        flash('تم توصيل الشحنة للعميل 🚚', 'success')

    # === التعديل الجذري هنا (التحصيل) ===
    elif action == 'settle':
        if order.shipping_status == 'settled':
            flash('تم التحصيل مسبقاً!', 'warning')
            return redirect(request.referrer)

        account_id = request.form.get('account_id')
        account = MoneyAccount.query.get(account_id)

        if not account:
            flash('يجب اختيار خزينة لإيداع المبلغ!', 'danger')
            return redirect(request.referrer)

        company = ShippingCompany.query.get(order.shipping_company_id)
        amount_collected = order.amount_due
        calculated_fee = 0.0

        if company and amount_collected > 0:
            calculated_fee += company.fee_first_1k
            if amount_collected > 1000:
                extra_amount = amount_collected - 1000
                thousands_count = math.ceil(extra_amount / 1000)
                calculated_fee += thousands_count * company.fee_extra_1k

        net_income = amount_collected - calculated_fee

        # === التعديل هنا: تصفير المبلغ المستحق ===
        order.amount_due = 0  # <--- هذا السطر هو الحل لجعل الفاتورة "خالص"
        order.shipping_fee = calculated_fee
        order.shipping_status = 'settled'
        order.shipping_settled_date = cairo_now()

        if net_income > 0:
            account.balance = round(account.balance + net_income, 1)
            db.session.add(FinancialTransaction(
                account_id=account.id,
                type='income',
                category='تحصيل شحن',
                amount=net_income,
                description=f"تحصيل شحنة فاتورة #{order.id} بوليصة {order.waybill_no} (العميل: {order.customer.name})",
                created_by_id=current_user.id,
                date=cairo_now()
            ))
            flash(f'تم التحصيل وإيداع الصافي ({net_income} ج.م) في {account.name} ✅', 'success')
        else:
            flash('تم تسوية الشحنة', 'warning')

    db.session.commit()
    return redirect(request.referrer)


@app.route('/shipping/bulk_collect', methods=['GET', 'POST'])
@login_required
def bulk_collect():
    if not current_user.has_perm('manage_shipping') and current_user.emp_code != 'EMP201' and current_user.role != 'general_manager':
        return "غير مصرح لك", 403

    accounts = MoneyAccount.query.all()

    if request.method == 'POST':
        waybill_input = request.form.get('waybills', '')
        account_id = request.form.get('account_id')
        account = MoneyAccount.query.get(account_id)

        if not account:
            flash('يجب اختيار خزينة!', 'danger')
            return redirect(request.referrer)

        # تفصيل أرقام البوالص (فصل بسطر جديد أو فاصلة أو مسافة)

        waybill_numbers = [w.strip() for w in re.split(r'[,\n\r\s]+', waybill_input) if w.strip()]

        if not waybill_numbers:
            flash('يجب إدخال رقم بوليصة واحد على الأقل!', 'danger')
            return redirect(request.referrer)

        settled_count = 0
        total_net = 0
        errors = []

        for wb in waybill_numbers:
            order = SaleOrder.query.filter_by(waybill_no=wb).first()
            if not order:
                errors.append(f'البوليصة {wb}: غير موجودة')
                continue
            if order.shipping_status == 'settled':
                errors.append(f'البوليصة {wb}: محصلة مسبقاً')
                continue
            if order.shipping_status not in ['shipped', 'delivered']:
                errors.append(f'البوليصة {wb}: حالة غير صالحة ({order.shipping_status})')
                continue

            # حساب المصاريف
            company = ShippingCompany.query.get(order.shipping_company_id)
            amount_collected = order.amount_due
            calculated_fee = 0.0
            if company and amount_collected > 0:
                calculated_fee += company.fee_first_1k
                if amount_collected > 1000:
                    extra_amount = amount_collected - 1000
                    thousands_count = math.ceil(extra_amount / 1000)
                    calculated_fee += thousands_count * company.fee_extra_1k

            net_income = amount_collected - calculated_fee

            order.amount_due = 0
            order.shipping_fee = calculated_fee
            order.shipping_status = 'settled'
            order.shipping_settled_date = cairo_now()

            if net_income > 0:
                account.balance = round(account.balance + net_income, 1)
                db.session.add(FinancialTransaction(
                    account_id=account.id,
                    type='income',
                    category='تحصيل شحن',
                    amount=net_income,
                    description=f"تحصيل جماعي - فاتورة #{order.id} بوليصة {order.waybill_no} (العميل: {order.customer.name})",
                    created_by_id=current_user.id,
                    date=cairo_now()
                ))
                total_net += net_income

            settled_count += 1

        db.session.commit()

        if settled_count > 0:
            flash(f'✅ تم تحصيل {settled_count} شحنة بإجمالي صافي {total_net} ج.م في {account.name}', 'success')
        for e in errors:
            flash(f'⚠️ {e}', 'warning')

        return redirect(url_for('bulk_collect'))

    return render_template('bulk_collect.html', accounts=accounts)


@app.route('/shipping/companies', methods=['GET', 'POST'])
@permission_required('manage_shipping')
def shipping_companies():
    if request.method == 'POST':
        name = request.form.get('name')
        phone = request.form.get('phone')
        try:
            fee_first = float(request.form.get('fee_first') or 0)
            fee_extra = float(request.form.get('fee_extra') or 0)
        except ValueError:
            fee_first = 0.0
            fee_extra = 0.0
        if name:
            db.session.add(ShippingCompany(name=name, phone=phone, cs_number="-", fee_first_1k=fee_first, fee_extra_1k=fee_extra))
            db.session.commit()
            flash('تم حفظ شركة الشحن ونظام التحصيل ✅', 'success')
        else: flash('اسم الشركة مطلوب', 'warning')
        return redirect(url_for('shipping_companies'))
    return render_template('shipping_companies.html', companies=ShippingCompany.query.all())


@app.route('/shipping/company/edit/<int:id>', methods=['POST'])
@permission_required('manage_shipping')
def edit_shipping_company(id):
    comp = ShippingCompany.query.get_or_404(id)
    comp.name = request.form.get('name')
    comp.phone = request.form.get('phone')
    try:
        comp.fee_first_1k = float(request.form.get('fee_first') or 0)
        comp.fee_extra_1k = float(request.form.get('fee_extra') or 0)
    except: pass
    db.session.commit()
    flash('تم تعديل البيانات بنجاح ✅', 'success')
    return redirect(url_for('shipping_companies'))


@app.route('/shipping/company/delete/<int:id>')
@permission_required('manage_shipping')
def delete_shipping_company(id):
    comp = ShippingCompany.query.get_or_404(id)
    if comp.orders: flash('لا يمكن الحذف', 'warning')
    else: db.session.delete(comp); db.session.commit(); flash('تم', 'success')
    return redirect(url_for('shipping_companies'))


@app.route('/shipping/company/<int:id>')
@permission_required('manage_shipping')
def shipping_company_profile(id):
    company = ShippingCompany.query.get_or_404(id)
    orders = SaleOrder.query.filter_by(shipping_company_id=id).order_by(SaleOrder.date.desc()).all()
    pending = sum(o.amount_due for o in orders if o.shipping_status in ['shipped', 'delivered'])
    collected = sum(o.amount_due for o in orders if o.shipping_status == 'settled')
    return render_template('shipping_company_profile.html', company=company, orders=orders, total_orders=len(orders), pending_money=pending, collected_money=collected)




@app.route('/shipping/daily_report')
@login_required
def shipping_daily_report():
    if not current_user.has_perm('view_shipping') and current_user.emp_code != 'EMP201':
        return "غير مصرح لك", 403

    # 1. تحديد نطاق التاريخ (الافتراضي: من أول الشهر الحالي لحد النهاردة)
    today = date.today()
    default_start = today.replace(day=1).strftime('%Y-%m-%d') # أول يوم في الشهر
    default_end = today.strftime('%Y-%m-%d') # النهاردة

    start_date = request.args.get('start_date', default_start)
    end_date = request.args.get('end_date', default_end)

    # 2. جلب الفواتير المحصلة في هذه الفترة (حسب تاريخ التحصيل الفعلي مش تاريخ الفاتورة)
    orders = SaleOrder.query.filter(
        SaleOrder.shipping_status == 'settled',
        cast(func.coalesce(SaleOrder.shipping_settled_date, SaleOrder.date), Date) >= start_date,
        cast(func.coalesce(SaleOrder.shipping_settled_date, SaleOrder.date), Date) <= end_date
    ).order_by(func.coalesce(SaleOrder.shipping_settled_date, SaleOrder.date).desc()).all()

    # 3. الحسابات
    report_data = []
    totals = {
        'total_collected': 0.0,
        'total_fees': 0.0,
        'total_net': 0.0
    }

    for o in orders:
        amount_collected_from_customer = o.final_total - o.paid_upfront
        shipping_fee = o.shipping_fee or 0.0
        net_income = amount_collected_from_customer - shipping_fee

        # عرض تاريخ التحصيل الفعلي إن وجد
        settled_str = o.shipping_settled_date.strftime('%Y-%m-%d') if o.shipping_settled_date else o.date.strftime('%Y-%m-%d')
        
        report_data.append({
            'invoice_date': o.date.strftime('%Y-%m-%d'), # تاريخ الفاتورة الأساسي
            'date': settled_str,                         # تاريخ التحصيل (للعرض في الجدول)
            'waybill': o.waybill_no,
            'customer': o.customer.name if o.customer else "عميل نقدي",
            'collected': round(amount_collected_from_customer, 2),
            'fee': round(shipping_fee, 2),
            'net': round(net_income, 2)
        })

        totals['total_collected'] += amount_collected_from_customer
        totals['total_fees'] += shipping_fee
        totals['total_net'] += net_income

    return render_template('shipping_daily_report.html',
                           orders=report_data,
                           totals=totals,
                           start_date=start_date,
                           end_date=end_date)
