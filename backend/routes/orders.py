"""
Routes: orders
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

def update_monthly_commissions(sales_rep_id, ref_date):
    """
    دالة تعيد حساب عمولات الشهر بالكامل للموظف ومديره
    كل شهر مستقل بذاته - بدون تراكم أو تسويات بأثر رجعي
    """
    try:
        # 1. تحديد الموظف والشريك (المدير)
        sales_rep = User.query.get(sales_rep_id)
        if not sales_rep: return

        partner = None
        if sales_rep.role == 'manager':
            partner = sales_rep
        elif sales_rep.manager_id:
            partner = User.query.get(sales_rep.manager_id)

        if not partner or partner.role != 'manager': return

        # 2. تحديد حدود الشهر
        target_month_str = ref_date.strftime('%Y-%m')
        target_month_start = ref_date.replace(day=1, hour=0, minute=0, second=0)
        next_month = (target_month_start.replace(day=28) + timedelta(days=4)).replace(day=1)

        # 3. حساب إجمالي مبيعات الشهر فقط (لتحديد الشريحة)
        monthly_sales = db.session.query(func.sum(SaleItem.quantity))\
            .join(SaleOrder)\
            .filter(SaleOrder.user_id == sales_rep.id,
                    SaleOrder.is_proforma == False,
                    SaleOrder.date >= target_month_start,
                    SaleOrder.date < next_month)\
            .scalar() or 0

        # خصم المرتجعات لنفس الشهر
        monthly_returns = db.session.query(func.sum(ReturnInvoice.total_qty))\
            .join(SaleOrder)\
            .filter(SaleOrder.user_id == sales_rep.id,
                    SaleOrder.date >= target_month_start,
                    SaleOrder.date < next_month)\
            .scalar() or 0

        total_monthly_items = max(0, monthly_sales - monthly_returns)

        # 4. تحديد سعر عمولة الموظفة (بناءً على صافي مبيعات الشهر)
        rate_per_item = 0.0
        if sales_rep.job_type == 'tiered_sales' and sales_rep.commission_rules:
            try:
                tiers = json.loads(sales_rep.commission_rules)
                for tier in tiers:
                    t_min = float(tier.get('min', 0))
                    t_max = float(tier.get('max', 999999))
                    t_val = float(tier.get('val', 0))
                    if t_min <= total_monthly_items <= t_max:
                        rate_per_item = t_val
                        break
            except: pass
        elif sales_rep.commission_value:
            rate_per_item = float(sales_rep.commission_value)

        # LOGGING
        with open('debug_comm_log.txt', 'a', encoding='utf-8') as f:
            f.write(f"\n--- DEBUG [{target_month_str}]: {sales_rep.fullname} ---\n")
            f.write(f"Monthly Sales: {monthly_sales}, Monthly Returns: {monthly_returns}, Net: {total_monthly_items}\n")
            f.write(f"Rate: {rate_per_item}\n")

        # 5. حذف التسويات القديمة (اللي ملهاش order_id) الخاصة بالشهر ده
        PartnerTransaction.query.filter(
            PartnerTransaction.partner_id == partner.id,
            PartnerTransaction.type == 'sub_commission',
            PartnerTransaction.order_id == None,
            PartnerTransaction.description.like(f"%{sales_rep.fullname}%"),
            PartnerTransaction.date >= target_month_start,
            PartnerTransaction.date < next_month
        ).delete(synchronize_session=False)

        # 6. تحديث فواتير الشهر
        monthly_orders = SaleOrder.query.filter(
            SaleOrder.user_id == sales_rep.id,
            SaleOrder.is_proforma == False,
            SaleOrder.date >= target_month_start,
            SaleOrder.date < next_month
        ).all()

        total_month_comm = 0.0

        for order in monthly_orders:
            # أ) تنظيف القديم — فقط حركات نفس الشهر (حتى لا تحذف مرتجعات الأشهر السابقة)
            PartnerTransaction.query.filter(
                PartnerTransaction.order_id == order.id,
                PartnerTransaction.type.in_(['commission_gross', 'sub_commission']),
                PartnerTransaction.date >= target_month_start,
                PartnerTransaction.date < next_month
            ).delete(synchronize_session=False)

            # ب) حساب صافي الفاتورة — query مباشر لتجنب cache العلاقات (مرتجعات نفس الشهر فقط)
            gross_qty = sum(item.quantity for item in order.items)
            returned_qty = db.session.query(func.sum(ReturnInvoice.total_qty)).filter(
                ReturnInvoice.order_id == order.id,
                ReturnInvoice.date >= target_month_start,
                ReturnInvoice.date < next_month
            ).scalar() or 0
            net_qty = max(0, gross_qty - returned_qty)
            
            if net_qty <= 0: continue

            # ج) عمولة الشريك (Gross) - من البروفايل
            partner_rate = float(partner.commission_value or 13.0)
            db.session.add(PartnerTransaction(
                partner_id=partner.id,
                order_id=order.id,
                type='commission_gross',
                amount=net_qty * partner_rate,
                description=f"عمولة ({net_qty} قطعة × {partner_rate}) - فاتورة مبيعات ({sales_rep.fullname})",
                date=order.date
            ))

            # د) عمولة الموظفة (تتخصم من الشريك)
            if sales_rep.id != partner.id and rate_per_item > 0:
                girl_comm = net_qty * rate_per_item
                total_month_comm += girl_comm
                
                db.session.add(PartnerTransaction(
                    partner_id=partner.id,
                    order_id=order.id,
                    type='sub_commission',
                    amount=-girl_comm,
                    description=f"عمولة ({sales_rep.fullname}) - شهر {target_month_str} ({total_monthly_items} قطعة، فئة {rate_per_item})",
                    date=order.date
                ))

        with open('debug_comm_log.txt', 'a', encoding='utf-8') as f:
            f.write(f"Total Month Commission: {total_month_comm}\n")

        db.session.commit()
        print(f"✅ Updated monthly commissions for Partner {partner.fullname} from Sales {sales_rep.fullname}")

    except Exception as e:
        print(f"❌ Error updating commissions: {e}")
        # Note: Do not rollback here because it cancels the entire SaleOrder creation.



@app.route('/api/process_order', methods=['POST'])
@login_required
def process_order():
    data = request.get_json()

    # 1. استلام البيانات الأساسية
    cart = data.get('cart', [])
    payments = data.get('payments', []) # قائمة الدفعات [{'account_id': 1, 'amount': 100}, ...]
    customer_id = data.get('customer_id')
    packer_id = data.get('packer_id') or None
    is_proforma = data.get('is_proforma', False)
    is_shipping = data.get('is_shipping', False)
    shipping_company_id = data.get('shipping_company_id')
    is_office = data.get('is_office_invoice', False) # فاتورة المكتب (تكلفة + 5)

    # معالجة التاريخ
    date_str = data.get('date')
    if date_str:
        try: order_date = datetime.strptime(date_str, '%Y-%m-%dT%H:%M')
        except ValueError: order_date = cairo_now()
    else: order_date = cairo_now()

    discount = float(data.get('discount') or 0)

    # === [أ] معالجة تعديل المسودة (حذف القديم) ===
    old_order_id = data.get('old_order_id')
    if old_order_id:
        old_order = SaleOrder.query.get(old_order_id)
        # شرط أمان: نحذف فقط لو كانت مسودة، عشان منمسحش فاتورة حقيقية بالغلط
        if old_order and old_order.is_proforma:
            SaleItem.query.filter_by(order_id=old_order.id).delete()
            db.session.delete(old_order)
            db.session.flush() # تنفيذ الحذف فوراً لتفريغ البضاعة المحجوزة نظرياً

    # === [ب] التحقق من المخزون (لو مش عرض سعر) ===
    if not is_proforma:
        for item in cart:
            qty_needed = int(item['qty'])
            if qty_needed <= 0: continue

            var = ProductVariant.query.get(item['id'])
            if not var:
                return jsonify({'error': f'المنتج كود {item["id"]} غير موجود'}), 400

            if var.stock < qty_needed:
                return jsonify({'error': f'عفواً، الكمية غير كافية للمنتج: {var.model.name} (المتاح: {var.stock})'}), 400

    # === [ج] تحديد البائع الفعلي ===
    actual_seller_id = current_user.id
    # لو المدير بيسجل باسم موظف تاني
    if current_user.role in ['manager', 'general_manager'] and data.get('sales_rep_id'):
        try: actual_seller_id = int(data.get('sales_rep_id'))
        except: pass

    seller_user = User.query.get(actual_seller_id)
    seller_code = seller_user.emp_code if seller_user else ""

    # === [د] حساب إجمالي المدفوع ===
    paid_upfront = sum(float(p.get('amount', 0)) for p in payments)
    
    # تصحيح: عروض السعر لا يجب أن تحتوي على مدفوعات
    if is_proforma:
        paid_upfront = 0
        payments = [] # تفريغ القائمة لمنع المعالجة اللاحقة
        
    # === [هـ] إنشاء الأوردر ===
    order = SaleOrder(
        user_id=actual_seller_id,
        customer_id=customer_id,
        packer_id=packer_id,
        date=order_date,
        is_shipping=is_shipping,
        shipping_company_id=shipping_company_id,
        shipping_fee=0.0,
        paid_upfront=paid_upfront,
        is_proforma=is_proforma,
        discount=discount,
        sales_rep_code=seller_code
    )
    db.session.add(order)
    db.session.flush() # للحصول على ID الفاتورة

    total_amount = 0

    # === [و] إضافة المنتجات وحساب الأسعار ===
    for item in cart:
        qty = int(item['qty'])
        if qty <= 0: continue

        variant = ProductVariant.query.get(item['id'])

        # منطق التسعير (مكتب vs عادي)
        if is_office and current_user.fullname == "أحمد عبد الفتاح":
            unit_price = (variant.cost_price or 0) + 5
        else:
            unit_price = float(item['price'])

        item_total = unit_price * qty
        total_amount += item_total

        # إضافة البند للفاتورة
        db.session.add(SaleItem(
            order_id=order.id,
            variant_id=item['id'],
            quantity=qty,
            unit_price=unit_price,
            total_price=item_total
        ))

        # خصم المخزون (لو مش عرض سعر)
        if not is_proforma:
            variant.stock -= qty
            db.session.add(StockMovement(
                variant_id=variant.id,
                user_id=current_user.id,
                quantity_change=-qty,
                reason=f"بيع فاتورة #{order.id}"
            ))

    # === [ز] تحديث مجاميع الفاتورة والعميل ===
    order.total_amount = total_amount
    order.final_total = total_amount - discount
    order.amount_due = round(order.final_total - paid_upfront, 2)

    # حالة الشحن
    if is_proforma: order.shipping_status = 'proforma'
    elif is_shipping: order.shipping_status = 'none' # عشان تظهر في لوحة الشحن
    else: order.shipping_status = 'settled'

    # تحديث رصيد العميل (إضافة المديونية)
    if not is_proforma and customer_id:
        customer = Customer.query.get(customer_id)
        if customer and order.amount_due > 0:
            customer.balance = (customer.balance or 0) + order.amount_due

    # === [ح] معالجة الدفعات المالية (Payments Loop) ===
    if not is_proforma and payments:
        for pay in payments:
            try:
                amt = float(pay.get('amount', 0))
                acc_id_raw = pay.get('account_id')
                acc_id = int(acc_id_raw) if acc_id_raw else None

                if amt > 0:
                    # لو مفيش خزنة محددة، نستخدم خزنة المكتب تحت (23) احتياطياً
                    if not acc_id:
                        acc_id = 23

                    account = MoneyAccount.query.get(acc_id)
                    if account:
                        account.balance = round(account.balance + amt, 1) # زيادة رصيد الخزنة
                        db.session.add(account) # تأكيد التحديث

                        db.session.add(FinancialTransaction(
                            type='income',
                            category='مبيعات',
                            amount=amt,
                            description=f"تحصيل فاتورة #{order.id} (دفعة) - {order.customer.name if order.customer else 'عميل نقدي'}",
                            date=cairo_now(),
                            created_by_id=current_user.id,
                            account_id=account.id
                        ))
            except Exception as e:
                print(f"Error processing payment for order {order.id}: {e}")

    # === [ط] تسجيل العمولات والخصومات (على الشركاء) ===
    if not is_proforma:
        # تحديد الشريك المسؤول (المدير المباشر)
        partner = None
        if seller_user.role == 'manager': partner = seller_user
        elif seller_user.manager_id:
            mgr = User.query.get(seller_user.manager_id)
            if mgr and mgr.role == 'manager': partner = mgr

        # 1. خصم التخفيض من الشريك (لو فيه خصم)
        if partner and discount > 0:
            db.session.add(PartnerTransaction(
                partner_id=partner.id,
                order_id=order.id,
                type='discount_deduction',
                amount=-discount,
                description=f"خصم ممنوح للعميل - فاتورة #{order.id}"
            ))

        # 2. تحديث عمولات الشريك والسيلز (commission_gross + sub_commission)
        # الدالة دي بتحسب commission_gross أوتوماتيك لكل فواتير الشهر
        update_monthly_commissions(actual_seller_id, order_date)

    db.session.commit()
    return jsonify({'success': True, 'order_id': order.id})
