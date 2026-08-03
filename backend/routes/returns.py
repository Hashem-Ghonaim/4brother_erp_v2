"""
Routes: returns
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

from ..core import app, db, cairo_now, basedir, FACTORY_LAT, FACTORY_LNG, ALLOWED_RADIUS, allowed_file, BASE_DIR, SEASON_START, SEASON_END
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


@app.route('/returns')
@login_required
def returns_list():
    returns = ReturnInvoice.query.order_by(ReturnInvoice.date.desc()).all()
    return render_template('returns_list.html', returns=returns)


@app.route('/returns/add', methods=['GET', 'POST'])
@login_required
def add_return():
    # التحقق من الصلاحية (للمديرين فقط)
    if current_user.role not in ['manager', 'general_manager']:
        flash('عفواً، هذه الصلاحية للمديرين فقط 🚫', 'danger')
        return redirect(url_for('returns_list'))

    if request.method == 'POST':
        try:
            order_id = request.form.get('order_id')
            refund_method = request.form.get('refund_method') # 'cash' أو 'debt'
            refund_account_id = request.form.get('refund_account_id')

            try:
                shipping_loss = float(request.form.get('shipping_loss') or 0)
                missing_cost = float(request.form.get('missing_cost') or 0)
            except:
                shipping_loss = 0.0; missing_cost = 0.0

            missing_desc = request.form.get('missing_desc')
            notes = request.form.get('notes')

            order = SaleOrder.query.get(order_id)
            if not order:
                flash('رقم الفاتورة غير صحيح', 'danger'); return redirect(request.url)

            # التحقق من الكميات المتاحة للمرتجع (مع مراعاة المرتجعات السابقة)
            previous_returned = {}
            for prev_ret in order.return_invoices:
                # نحتاج نحسب الكميات المرتجعة سابقاً - لكن ReturnInvoice لا تخزن تفاصيل الأصناف
                pass

            # 2. معالجة المخزون وحساب قيمة البضاعة المرتجعة
            total_qty_returned = 0
            total_items_value = 0.0

            for item in order.items:
                returned_qty = int(request.form.get(f'qty_returned_{item.id}') or 0)
                if returned_qty > item.quantity:
                    flash(f'خطأ: الكمية المرتجعة أكبر من الموجودة في الفاتورة!', 'danger')
                    return redirect(request.url)

                if returned_qty > 0:
                    total_qty_returned += returned_qty
                    total_items_value += (returned_qty * item.unit_price)

                    if item.variant:
                        item.variant.stock += returned_qty
                        db.session.add(StockMovement(
                            variant_id=item.variant.id, user_id=current_user.id,
                            quantity_change=returned_qty, reason=f"مرتجع فاتورة #{order.id}"
                        ))

            if total_qty_returned == 0:
                flash('برجاء تحديد الأصناف المرتجعة', 'warning'); return redirect(request.url)

            # 3. حساب القيمة الصافية للمرتجع (بعد خصم الشحن والتوالف)
            total_deduction = shipping_loss + missing_cost
            net_refund_value = total_items_value - total_deduction

            # 4. تسجيل فاتورة المرتجع في السيستم
            ret_inv = ReturnInvoice(
                order_id=order.id, shipping_loss=shipping_loss,
                missing_items_cost=missing_cost, missing_items_desc=missing_desc,
                total_deduction=total_deduction, created_by=current_user.id, notes=notes,
                total_qty=total_qty_returned # تخزين الكمية المرتجعة
            )
            db.session.add(ret_inv)
            
            original_total_qty = sum(item.quantity for item in order.items)
            historical_returned_qty = sum((ret.total_qty or 0) for ret in order.return_invoices)
            
            if (historical_returned_qty + total_qty_returned) >= original_total_qty:
                order.shipping_status = 'returned'
            elif order.shipping_status != 'settled':
                order.shipping_status = 'partial_return'

            # 5. التأثير المالي (الخزينة أو المديونية)
            if net_refund_value > 0:
                if refund_method == 'cash':
                    # رد نقدي من الخزينة (لو الزبون كان دافع)
                    account = MoneyAccount.query.get(refund_account_id)
                    if account:
                        account.balance = round(account.balance - net_refund_value, 1)
                        db.session.add(FinancialTransaction(
                            account_id=account.id, type='refund', category='مرتجعات مبيعات',
                            amount=-net_refund_value,
                            description=f"رد نقدي لمرتجع فاتورة #{order.id} (العميل: {order.customer.name if order.customer else 'نقدي'})",
                            created_by_id=current_user.id, date=cairo_now()
                        ))
                    else:
                        flash('يجب اختيار خزينة للرد النقدي!', 'danger'); return redirect(request.url)
                else:
                    # خصم من مديونية العميل (لو الزبون مدفعش أو عليه فلوس)
                    if order.customer:
                        order.customer.balance = (order.customer.balance or 0) - net_refund_value
                        db.session.add(FinancialTransaction(
                            type='debt_adjustment', category='تسوية مديونية', amount=0,
                            description=f"تسوية مديونية (مرتجع #{order.id}): خصم {net_refund_value} من حساب {order.customer.name}",
                            created_by_id=current_user.id, date=cairo_now()
                        ))

            # تحديث مديونية الفاتورة نفسها لتظهر كـ "خالصة" أو مخفضة
            order.amount_due = max(0, order.amount_due - net_refund_value)

            # 6. معالجة حسابات الشركاء (إلغاء الربح والعمولة عن القطع المرتجعة)
            sales_rep = User.query.get(order.user_id)
            partner = None
            if sales_rep.role == 'manager': partner = sales_rep
            elif sales_rep.manager_id:
                mgr = User.query.get(sales_rep.manager_id)
                if mgr and mgr.role == 'manager': partner = mgr

            if partner:
                # أ) إلغاء ربح الشريك عن القطع المرجعة
                partner_rate = float(partner.commission_value or 13.0)
                db.session.add(PartnerTransaction(
                    partner_id=partner.id, order_id=order.id, type='commission_gross',
                    amount=-(total_qty_returned * partner_rate),
                    description=f"خصم ربح قطع مرتجعة ({total_qty_returned} قطعة × {partner_rate}) - فاتورة #{order.id}"
                ))
                # ب) خصم خسائر الشحن أو التوالف من الشريك
                if total_deduction > 0:
                    db.session.add(PartnerTransaction(
                        partner_id=partner.id, order_id=order.id, type='return_penalty',
                        amount=-total_deduction, description=f"تحمل خسائر مرتجع فاتورة #{order.id}"
                    ))
                # ج) استرداد عمولة السيلز (ترجع لجيب المدير)
                if sales_rep.role == 'sales':
                    # حساب مبيعات الشهر الأصلي للفاتورة عشان نخصم العمولة بنفس الشريحة اللي اتحسبت بيها
                    month_start = order.date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                    if month_start.month == 12:
                        month_end = month_start.replace(year=month_start.year + 1, month=1)
                    else:
                        month_end = month_start.replace(month=month_start.month + 1)
                        
                    gross_items = db.session.query(func.sum(SaleItem.quantity)).join(SaleOrder).filter(
                        SaleOrder.user_id == sales_rep.id,
                        SaleOrder.is_proforma == False,
                        SaleOrder.date >= month_start,
                        SaleOrder.date < month_end
                    ).scalar() or 0
                    
                    returned_items_that_month = db.session.query(func.sum(ReturnInvoice.total_qty)).join(SaleOrder).filter(
                        SaleOrder.user_id == sales_rep.id,
                        ReturnInvoice.date >= month_start,
                        ReturnInvoice.date < month_end
                    ).scalar() or 0
                    
                    net_items_that_month = max(0, gross_items - returned_items_that_month)
                    
                    comm_to_reverse = calculate_user_commission(sales_rep, total_qty_returned, net_items_that_month)
                    if comm_to_reverse > 0:
                        db.session.add(PartnerTransaction(
                            partner_id=partner.id, order_id=order.id, type='sub_commission',
                            amount=comm_to_reverse, description=f"استرداد عمولة سيلز ({sales_rep.fullname}) عن مرتجع #{order.id}"
                        ))
                    # تسجيل المرتجع في ملف الموظفة لخصمه
                    current_month = cairo_now().strftime('%Y-%m')
                    order_month = order.date.strftime('%Y-%m')
                    
                    if current_month == order_month:
                        # مرتجع في نفس الشهر، يخصم كقطع من التارجت فقط
                        db.session.add(HRTransaction(
                            user_id=sales_rep.id, type='deduction', amount=0,
                            note=f"مرتجع فاتورة #{order.id} ({total_qty_returned} قطعة)", date=cairo_now()
                        ))
                    else:
                        # مرتجع قديم، يخصم كقيمة مالية
                        if comm_to_reverse > 0:
                            db.session.add(HRTransaction(
                                user_id=sales_rep.id, type='return_reversal', amount=comm_to_reverse,
                                note=f"استرداد عمولة مرتجع فاتورة قديمة #{order.id} ({total_qty_returned} قطعة)", date=cairo_now()
                            ))

            db.session.commit()

            # إعادة حساب عمولات الشريك لتعكس المرتجع
            from ..routes.invoices import update_monthly_commissions
            update_monthly_commissions(sales_rep.id, order.date)

            flash(f'تم تسجيل المرتجع بنجاح ✅ (صافي القيمة: {net_refund_value} ج.م)', 'success')
            return redirect(url_for('invoices'))

        except Exception as e:
            db.session.rollback()
            flash(f"حدث خطأ: {e}", "danger")
            return redirect(request.url)

    # GET: عرض الصفحة
    orders = SaleOrder.query.filter(SaleOrder.is_proforma==False).order_by(SaleOrder.id.desc()).all()
    accounts = MoneyAccount.query.all()
    return render_template('return_add.html', orders=orders, accounts=accounts)

@app.route('/api/get_order_details/<int:id>')
@login_required
def get_order_details(id):
    order = SaleOrder.query.get_or_404(id)
    
    # حساب الكميات اللي رجعت قبل كده من الفاتورة دي عن طريق حركات المخزون
    from backend.models import StockMovement
    movements = StockMovement.query.filter(StockMovement.reason.like(f"%مرتجع فاتورة #{order.id}%")).all()
    returned_qty_by_variant = {}
    for mv in movements:
        # حركة المرتجع بتزود المخزن، فالقيمة موجبة
        returned_qty_by_variant[mv.variant_id] = returned_qty_by_variant.get(mv.variant_id, 0) + mv.quantity_change
    
    items = []
    for item in order.items:
        returned_already = returned_qty_by_variant.get(item.variant_id, 0)
        available_qty = max(0, item.quantity - returned_already)
        if available_qty > 0:
            items.append({
                'id': item.id,
                'variant_id': item.variant.id,
                'name': item.variant.model.name,
                'qty': available_qty,
                'price': item.unit_price
            })

    return jsonify({
        'items': items,
        'total_amount': order.total_amount, # إجمالي الفاتورة
        'paid_upfront': order.paid_upfront, # العربون/المدفوع
        'amount_due': order.amount_due      # المتبقي/الدين
    })

# تأكد من إعداد مجلد الرفع في إعدادات التطبيق
# app.config['UPLOAD_FOLDER'] = 'static/uploads'

