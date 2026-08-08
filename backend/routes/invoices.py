"""
Routes: invoices
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

        # خصم المرتجعات في نفس الشهر (حسب تاريخ المرتجع، حتى لو البيع من شهر قديم)
        monthly_returns = db.session.query(func.sum(ReturnInvoice.total_qty))\
            .join(SaleOrder)\
            .filter(SaleOrder.user_id == sales_rep.id,
                    ReturnInvoice.date >= target_month_start,
                    ReturnInvoice.date < next_month)\
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

        # LOGGING (safe for read-only filesystems like Vercel)
        try:
            with open('debug_comm_log.txt', 'a', encoding='utf-8') as f:
                f.write(f"\n--- DEBUG [{target_month_str}]: {sales_rep.fullname} (ID:{sales_rep.id}) ---\n")
                f.write(f"Partner: {partner.fullname} (ID:{partner.id})\n")
                f.write(f"job_type: {sales_rep.job_type}, commission_value: {sales_rep.commission_value}, commission_rules: {sales_rep.commission_rules}\n")
                f.write(f"Monthly Sales: {monthly_sales}, Monthly Returns: {monthly_returns}, Net: {total_monthly_items}\n")
                f.write(f"Rate: {rate_per_item}\n")
        except:
            pass  # Vercel read-only filesystem

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

        try:
            with open('debug_comm_log.txt', 'a', encoding='utf-8') as f:
                f.write(f"Total Month Commission: {total_month_comm}\n")
        except:
            pass  # Vercel read-only filesystem

        db.session.commit()
        print(f"✅ Updated monthly commissions for Partner {partner.fullname} from Sales {sales_rep.fullname}")

    except Exception as e:
        print(f"❌ Error updating commissions: {e}")
        # Note: Do not rollback here because it cancels the entire transaction.



@app.route('/invoice/print/<int:id>')
@login_required
def print_invoice(id):
    order = SaleOrder.query.get_or_404(id)
    
    return_map = {}
    total_returned_qty = 0
    returned_items_amount = 0

    if order.return_invoices or order.shipping_status in ['returned', 'partial_return']:
        movements = StockMovement.query.filter(StockMovement.reason.like(f"%مرتجع فاتورة #{order.id}%")).all()
        items_by_variant = {i.variant_id: i for i in order.items}
        
        for mv in movements:
            if mv.variant_id:
                qty_chg = mv.quantity_change
                return_map[mv.variant_id] = return_map.get(mv.variant_id, 0) + qty_chg
                total_returned_qty += qty_chg
                
                sale_item = items_by_variant.get(mv.variant_id)
                if sale_item:
                    returned_items_amount += qty_chg * sale_item.unit_price

    after_total_amount = order.total_amount - returned_items_amount
    after_final_total = after_total_amount - order.discount
    
    return_stats = {
        'total_returned_qty': total_returned_qty,
        'returned_items_amount': returned_items_amount,
        'after_total_amount': after_total_amount,
        'after_final_total': after_final_total
    }
                    
    return render_template('invoice.html', order=order, return_map=return_map, return_stats=return_stats)
# === الكتالوج الرقمي (رابط عام للعملاء) ===

@app.route('/invoice/convert/<int:id>')
@login_required
def convert_to_invoice(id):
    order = SaleOrder.query.get_or_404(id)

    # التأكد أنها عرض سعر فعلاً
    if not order.is_proforma:
        return redirect(url_for('print_invoice', id=id))

    # 1. خصم الكميات من المخزن (لأنها أصبحت فاتورة فعلية)
    for item in order.items:
        if item.variant:
            item.variant.stock -= item.quantity
            db.session.add(StockMovement(
                variant_id=item.variant.id,
                user_id=current_user.id,
                quantity_change=-item.quantity,
                reason=f"تحويل عرض سعر #{order.id} لفاتورة"
            ))

    # 2. تحديث حالة الفاتورة
    order.is_proforma = False
    order.date = cairo_now() # تحديث التاريخ لوقت الاعتماد

    # === [التعديل هنا] ضبط حالة الشحن ===
    if order.is_shipping:
        # لو الفاتورة شحن، نخلي حالتها 'none' عشان تظهر في صفحة الشحن كشحنة جديدة
        order.shipping_status = 'none'
    else:
        # لو استلام محل، نعتبرها 'settled' (منتهية لوجيستياً)
        order.shipping_status = 'settled'

    # 3. تسجيل عمولات الشركاء (لأنها أصبحت بيعة حقيقية)
    if order.user_id:
        seller_user = db.session.get(User, order.user_id)
        partner = None
        if seller_user.role == 'manager':
            partner = seller_user
        elif seller_user.manager_id:
            mgr = db.session.get(User, seller_user.manager_id)
            if mgr and mgr.role == 'manager':
                partner = mgr

        # خصم التخفيض من الشريك (لو الفاتورة فيها خصم)
        if partner and order.discount and order.discount > 0:
            db.session.add(PartnerTransaction(
                partner_id=partner.id,
                order_id=order.id,
                type='discount_deduction',
                amount=-order.discount,
                description=f"خصم ممنوح للعميل - فاتورة #{order.id}",
                date=order.date
            ))

        # تحديث عمولات السيلز والشريك (commission_gross + sub_commission)
        update_monthly_commissions(order.user_id, order.date)

    db.session.commit()
    flash('تم اعتماد عرض السعر وتحويله لفاتورة بنجاح، وتم إدراجها في الشحن ✅', 'success')
    return redirect(url_for('print_invoice', id=id))


@app.route('/invoice/revert_to_draft/<int:order_id>', methods=['POST'])
@login_required
def revert_to_draft(order_id):
    """تحويل فاتورة تامة إلى مسودة (عرض سعر) مع عكس كل التأثيرات المالية والمخزنية"""
    if current_user.role != 'general_manager' and current_user.username != 'gm_ahmed':
        return jsonify({'success': False, 'message': 'هذه العملية متاحة فقط للمدير العام'}), 403

    try:
        order = SaleOrder.query.get(order_id)
        if not order:
            return jsonify({'success': False, 'message': 'الفاتورة غير موجودة'}), 404

        if order.is_proforma:
            return jsonify({'success': False, 'message': 'هذه الفاتورة مسودة بالفعل'}), 400

        # التحقق من عدم وجود مرتجعات
        if order.return_invoices and len(order.return_invoices) > 0:
            return jsonify({'success': False, 'message': 'لا يمكن تحويل فاتورة عليها مرتجعات إلى مسودة. احذف المرتجعات أولاً.'}), 400

        # === 1. إرجاع المخزون ===
        for item in order.items:
            if item.variant:
                item.variant.stock += item.quantity
                db.session.add(StockMovement(
                    variant_id=item.variant.id,
                    user_id=current_user.id,
                    quantity_change=item.quantity,
                    reason=f"تحويل فاتورة #{order_id} إلى مسودة"
                ))

        # === 2. عكس المعاملات المالية (إرجاع الفلوس من الخزنة) ===
        financial_txs = FinancialTransaction.query.filter(
            FinancialTransaction.description.like(f'%#{order_id}%'),
            FinancialTransaction.description.like('%فاتورة%')
        ).all()

        for tx in financial_txs:
            if tx.description and re.search(rf'فاتورة.*#{order_id}\b', tx.description):
                account = MoneyAccount.query.get(tx.account_id)
                if account:
                    if tx.type == 'income':
                        account.balance = round(account.balance - tx.amount, 1)
                    elif tx.type == 'expense':
                        account.balance = round(account.balance + tx.amount, 1)
                db.session.delete(tx)

        # === 3. حذف حركات الشركاء المرتبطة بالفاتورة ===
        db.session.execute(text("DELETE FROM partner_transaction WHERE order_id = :oid"), {'oid': order_id})

        # === 4. حذف سجلات HR المرتبطة بالفاتورة ===
        hr_txs = HRTransaction.query.filter(
            HRTransaction.note.like(f'%#{order_id}%'),
            HRTransaction.note.like('%فاتورة%')
        ).all()
        for htx in hr_txs:
            if htx.note and re.search(rf'فاتورة.*#{order_id}\b', htx.note):
                db.session.delete(htx)

        # === 5. تحويل الفاتورة لمسودة ===
        order.is_proforma = True
        order.shipping_status = None

        db.session.commit()

        # === 6. تحديث العمولات ===
        if order.user_id:
            update_monthly_commissions(order.user_id, order.date)

        return jsonify({'success': True, 'message': 'تم تحويل الفاتورة إلى مسودة بنجاح! يمكنك تعديلها الآن من قسم عروض الأسعار ✏️'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'خطأ: {str(e)}'}), 500

@app.route('/invoice/edit_proforma/<int:id>')
@login_required
def edit_proforma(id):
    order = SaleOrder.query.get_or_404(id)

    # التأكد أنها مسودة (عرض سعر)
    if not order.is_proforma:
        flash('لا يمكن تعديل فاتورة تم اعتمادها، فقط عروض الأسعار.', 'warning')
        return redirect(url_for('invoices'))

    # تجهيز البيانات لإرسالها للجافاسكريبت 
    order_data = {
        'id': order.id,
        'user_id': order.user_id,
        'customer_id': order.customer_id,
        'discount': order.discount,
        'paid_upfront': order.paid_upfront,
        'is_shipping': order.is_shipping,
        'shipping_company_id': order.shipping_company_id,
        'is_office_invoice': False,
        'packer_id': order.packer_id,
        'items': []
    }

    for item in order.items:
        order_data['items'].append({
            'id': item.variant.id,
            'name': item.variant.model.name,
            'price': item.unit_price,
            'qty': item.quantity,
            'stock': item.variant.stock, # عشان الـ Validation
            'cost_price': item.variant.model.cost_price if item.variant.model else 0,
            'barcode': item.variant.barcode or ''
        })

    # إرسال نفس البيانات التي تحتاجها صفحة POS العادية + بيانات الفاتورة
    accessible_ids = get_accessible_users()
    customers = Customer.query.filter(
        or_(Customer.created_by_id.in_(accessible_ids), Customer.name == "عميل نقدي")
    ).order_by(Customer.id.desc()).all()

    return render_template('pos.html',
                           categories=Category.query.all(),
                           products=ProductVariant.query.join(ProductModel).all(),
                           customers=customers,
                           shipping_companies=ShippingCompany.query.all(),
                           money_accounts=MoneyAccount.query.all(),
                           all_employees=User.query.all(),
                           # المتغير الجديد المهم جداً 👇
                           edit_order_data=order_data)

@app.route('/invoice/delete/<int:order_id>', methods=['GET', 'POST'])
@login_required
def delete_invoice(order_id):
    if not current_user.has_perm('manage_orders') and current_user.role != 'general_manager':
        return jsonify({'success': False, 'message': 'غير مصرح لك بحذف الفواتير'}), 403

    try:
        order = SaleOrder.query.get(order_id)
        if not order:
            return jsonify({'success': False, 'message': 'الفاتورة غير موجودة'}), 404

        # =========================================================
        # === 1. إرجاع الأموال للخزينة وتصحيح الرصيد (الجديد) ===
        # =========================================================
        # نبحث عن كل المعاملات المالية المرتبطة بهذه الفاتورة
        financial_txs = FinancialTransaction.query.filter(
            FinancialTransaction.description.like(f'%#{order_id}%'),
            FinancialTransaction.description.like('%فاتورة%')
        ).all()

        valid_financial_txs = []
        for tx in financial_txs:
            if tx.description and re.search(rf'فاتورة.*#{order_id}\b', tx.description):
                valid_financial_txs.append(tx)

        for tx in valid_financial_txs:
            account = MoneyAccount.query.get(tx.account_id)
            if account:
                if tx.type == 'income':
                    # لو كانت الفاتورة دخل (بيع)، نطرح المبلغ من الخزنة
                    account.balance = round(account.balance - tx.amount, 1)
                elif tx.type == 'expense':
                    # لو كانت مصروف (نادر في البيع)، نرجعه للخزنة
                    account.balance = round(account.balance + tx.amount, 1)
            # حذف سجل المعاملة بعد تعديل الرصيد
            db.session.delete(tx)

        # = ::::: بقية الكود كما هو مع التأكد من الحذف الصحيح ::::: =

        # 2. إرجاع المخزون
        should_restore_stock = (not order.is_proforma) and (len(order.return_invoices) == 0)
        if should_restore_stock:
            for item in order.items:
                db.session.execute(text(f"UPDATE product_variant SET stock = stock + :qty WHERE id = :vid"),
                                   {'qty': item.quantity, 'vid': item.variant_id})
                try:
                    db.session.add(StockMovement(
                        variant_id=item.variant_id,
                        user_id=current_user.id,
                        quantity_change=item.quantity,
                        reason=f"حذف فاتورة #{order_id}"
                    ))
                except: pass

        # 3. تنظيف الحسابات الأخرى
        hr_txs = HRTransaction.query.filter(
            HRTransaction.note.like(f'%#{order_id}%'),
            HRTransaction.note.like('%فاتورة%')
        ).all()
        for htx in hr_txs:
            if htx.note and re.search(rf'فاتورة.*#{order_id}\b', htx.note):
                db.session.delete(htx)
                
        db.session.execute(text("DELETE FROM partner_transaction WHERE order_id = :oid"), {'oid': order_id})
        db.session.execute(text("DELETE FROM return_invoice WHERE order_id = :oid"), {'oid': order_id})

        # 4. حذف الأصناف والفاتورة
        db.session.execute(text("DELETE FROM sale_item WHERE order_id = :oid"), {'oid': order_id})
        db.session.execute(text("DELETE FROM sale_order WHERE id = :oid"), {'oid': order_id})

        db.session.commit()

        # تحديث العمولات
        if order.user_id:
            update_monthly_commissions(order.user_id, order.date)

        return jsonify({'success': True, 'message': 'تم حذف الفاتورة، تصفير المديونية، وإرجاع الفلوس للخزنة بنجاح! 💰🗑️'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'خطأ قاعدة بيانات: {str(e)}'}), 500

@app.route('/invoice/edit/<int:id>', methods=['GET', 'POST'])
@general_manager_required
def edit_invoice(id):
    order = SaleOrder.query.get_or_404(id)
    if order.date.date() != cairo_now().date(): flash('لا يمكن تعديل فواتير سابقة', 'warning'); return redirect(url_for('invoices'))
    if request.method == 'POST':
        for item in order.items:
            item.variant.stock += item.quantity
            db.session.delete(item)
        p_names = request.form.getlist('product_name[]')
        qtys = request.form.getlist('qty[]')
        total = 0
        for i in range(len(p_names)):
            p_name = p_names[i]
            try: qty = int(qtys[i])
            except: qty = 0
            if p_name and qty > 0:
                model = ProductModel.query.filter_by(name=p_name).first()
                if model:
                    variant = model.variants[0]
                    variant.stock -= qty
                    price = variant.sell_price
                    total += price * qty
                    db.session.add(SaleItem(order=order, variant_id=variant.id, quantity=qty, unit_price=price, total_price=price*qty))
        order.discount = float(request.form.get('discount', 0))
        order.total_amount = total
        order.final_total = total - order.discount
        # التصحيح: إزالة shipping_fee من المعادلة عشان متتحسبش مرتين
        order.amount_due = order.final_total - order.paid_upfront

        # === [إصلاح] تحديث عمولات الشركاء بعد التعديل ===
        if not order.is_proforma:
            seller_user = db.session.get(User, order.user_id)
            partner = None
            if seller_user.role == 'manager':
                partner = seller_user
            elif seller_user.manager_id:
                mgr = db.session.get(User, seller_user.manager_id)
                if mgr and mgr.role == 'manager':
                    partner = mgr

            if partner:
                # حذف الخصم القديم وإنشاء الجديد
                PartnerTransaction.query.filter_by(
                    order_id=order.id, type='discount_deduction'
                ).delete(synchronize_session=False)

                if order.discount > 0:
                    db.session.add(PartnerTransaction(
                        partner_id=partner.id,
                        order_id=order.id,
                        type='discount_deduction',
                        amount=-order.discount,
                        description=f"خصم ممنوح للعميل - فاتورة #{order.id}",
                        date=order.date
                    ))

            # تحديث commission_gross و sub_commission
            update_monthly_commissions(order.user_id, order.date)

        db.session.commit(); return redirect(url_for('invoices'))
    return render_template('edit_invoice.html', order=order, products=ProductModel.query.all())


@app.route('/invoices')
@login_required
def invoices():
    if not current_user.has_perm('view_invoices'):
        return "غير مصرح لك", 403

    # 1. تحديد نوع الفواتير المطلوبة من الرابط
    # إذا كان الرابط ?type=proforma نعرض المسودات، غير ذلك نعرض الفواتير التامة
    show_proforma = (request.args.get('type') == 'proforma')

    accessible_ids = get_accessible_users()
    
    # جلب قائمة الكائنات للمستخدمين المتاحين (عشان نعرضهم في الفلتر)
    accessible_users_list = User.query.filter(User.id.in_(accessible_ids)).all()

    # 2. الاستعلام الأساسي (مع الفلتر الجديد)
    query = SaleOrder.query.filter(SaleOrder.user_id.in_(accessible_ids))

    # فلتر الموظف (لو تم اختياره)
    selected_user_id = request.args.get('user_id')
    if selected_user_id and selected_user_id.isdigit():
        uid = int(selected_user_id)
        # التأكد إن الموظف المختار ضمن صلاحياتي
        if uid in accessible_ids:
            query = query.filter(SaleOrder.user_id == uid)

    # فلتر التاريخ (من - إلى)
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    if start_date:
        query = query.filter(SaleOrder.date >= f"{start_date} 00:00:00")
    if end_date:
        query = query.filter(SaleOrder.date <= f"{end_date} 23:59:59")

    if show_proforma:
        # عرض عروض الأسعار فقط
        query = query.filter(SaleOrder.is_proforma == True)
    else:
        # عرض الفواتير التامة فقط
        query = query.filter(SaleOrder.is_proforma == False)

    orders = query.order_by(SaleOrder.date.desc()).all()

    # 3. حساب الإجماليات (للمدير العام فقط) - سيتم الحساب بناءً على القائمة المفلترة
    is_gm = (current_user.username == 'gm_ahmed')
    grand_totals = {
        'total_cost': 0, 'total_sell': 0, 'total_profit': 0,
        'total_comm': 0, 'total_company_net': 0, 'total_items': 0,
        'total_gross_items': 0, 'total_returns_in_period': 0,
        'total_ret_shipping': 0, 'total_ret_missing': 0, 'total_ret_deduction': 0,
        'total_shipping': 0
    }

    # حساب إجمالي المرتجعات في الفترة (حسب تاريخ المرتجع، حتى لو البيع من شهر آخر)
    ret_query = db.session.query(func.sum(ReturnInvoice.total_qty))
    if selected_user_id and selected_user_id.isdigit() and int(selected_user_id) in accessible_ids:
        ret_query = ret_query.join(SaleOrder).filter(SaleOrder.user_id == int(selected_user_id))
    if start_date:
        ret_query = ret_query.filter(ReturnInvoice.date >= f"{start_date} 00:00:00")
    if end_date:
        ret_query = ret_query.filter(ReturnInvoice.date <= f"{end_date} 23:59:59")
    grand_totals['total_returns_in_period'] = ret_query.scalar() or 0

    monthly_sales_cache = {}

    def get_monthly_net_items(uid, o_date):
        if not uid or not o_date: return 0
        key = (uid, o_date.year, o_date.month)
        if key in monthly_sales_cache:
            return monthly_sales_cache[key]
            
        from datetime import datetime
        start_date = datetime(o_date.year, o_date.month, 1)
        if o_date.month == 12:
            end_date = datetime(o_date.year + 1, 1, 1)
        else:
            end_date = datetime(o_date.year, o_date.month + 1, 1)
            
        gross_items = db.session.query(func.sum(SaleItem.quantity)).join(SaleOrder).filter(
            SaleOrder.user_id == uid, 
            SaleOrder.is_proforma == False, 
            SaleOrder.date >= start_date, 
            SaleOrder.date < end_date
        ).scalar() or 0
        
        returns = db.session.query(func.sum(ReturnInvoice.total_qty))\
            .join(SaleOrder)\
            .filter(SaleOrder.user_id == uid,
                    ReturnInvoice.date >= start_date,
                    ReturnInvoice.date < end_date).scalar() or 0
        
        net_items = gross_items - returns
        if net_items < 0: net_items = 0
        
        monthly_sales_cache[key] = net_items
        return net_items

    for o in orders:
        # === بيانات المرتجعات ===
        o.ret_date = None
        o.ret_shipping_loss = 0
        o.ret_missing_cost = 0
        o.ret_total_deduction = 0
        o.ret_total_qty = 0

        if o.return_invoices:
            latest = max(o.return_invoices, key=lambda r: r.date)
            o.ret_date = latest.date
            o.ret_shipping_loss = sum(r.shipping_loss or 0 for r in o.return_invoices)
            o.ret_missing_cost = sum(r.missing_items_cost or 0 for r in o.return_invoices)
            o.ret_total_deduction = sum(r.total_deduction or 0 for r in o.return_invoices)
            o.ret_total_qty = sum(r.total_qty or 0 for r in o.return_invoices)

        # نفس منطق الحساب القديم
        o.real_cost = sum((i.variant.cost_price or 0) * i.quantity for i in o.items if i.variant)

        # حساب الصافي بعد المرتجعات
        seller = db.session.get(User, o.user_id) if o.user_id else None
        o.est_comm = 0
        order_qty = sum(i.quantity for i in o.items)
        net_qty = max(0, order_qty - o.ret_total_qty)
        o.net_comm = 0
        o.display_qty = order_qty
        o.display_ret_qty = o.ret_total_qty

        # الإجمالي والتكلفة بعد خصم المرتجعات (بالأسعار الحقيقية)
        if order_qty > 0 and o.ret_total_qty > 0:
            # حساب قيمة القطع المرتجعة من حركات المخزون
            movements = StockMovement.query.filter(StockMovement.reason.like(f"%مرتجع فاتورة #{o.id}%")).all()
            returned_value = 0.0
            returned_cost = 0.0
            # ربط حركة المخزون بسعر القطعة في الفاتورة
            items_by_variant = {i.variant_id: i for i in o.items}
            for mv in movements:
                sale_item = items_by_variant.get(mv.variant_id)
                if sale_item:
                    returned_value += mv.quantity_change * sale_item.unit_price
                if mv.variant:
                    returned_cost += mv.quantity_change * (mv.variant.cost_price or 0)
            o.net_total = round((o.total_amount or 0) - returned_value, 2)
            o.net_cost = round(o.real_cost - returned_cost, 2)
        else:
            o.net_total = o.total_amount or 0
            o.net_cost = o.real_cost

        is_under_partner = False
        if seller:
            if seller.role == 'manager':
                is_under_partner = True
            elif seller.manager_id:
                mgr = db.session.get(User, seller.manager_id)
                if mgr and mgr.role == 'manager':
                    is_under_partner = True

            actual_discount = (o.discount or 0) if net_qty > 0 else 0

            if is_under_partner:
                partner_obj = seller if seller.role == 'manager' else db.session.get(User, seller.manager_id)
                p_rate = float(partner_obj.commission_value or 13.0) if partner_obj else 13.0
                o.est_comm = net_qty * p_rate
                o.net_comm = o.est_comm - actual_discount - o.ret_total_deduction
            else:
                monthly_net = get_monthly_net_items(seller.id, o.date)
                o.est_comm = calculate_user_commission(seller, net_qty, monthly_net)
                o.net_comm = o.est_comm

        actual_discount = (o.discount or 0) if net_qty > 0 else 0

        # حساب ربح الشركة بالصافي بعد المرتجعات
        if is_under_partner:
            revenue_for_company = o.net_total - (o.shipping_fee or 0)
            o.company_bears_discount = False
        else:
            revenue_for_company = o.net_total - actual_discount - (o.shipping_fee or 0)
            o.company_bears_discount = True if actual_discount > 0 else False

        o.gross_profit = round(revenue_for_company - o.net_cost, 2)
        o.company_net = round(o.gross_profit - o.net_comm, 2)

        if is_gm:
            grand_totals['total_cost'] = round(grand_totals['total_cost'] + o.net_cost, 2)
            grand_totals['total_sell'] = round(grand_totals['total_sell'] + o.net_total, 2)
            grand_totals['total_profit'] = round(grand_totals['total_profit'] + o.gross_profit, 2)
            grand_totals['total_comm'] = round(grand_totals['total_comm'] + o.net_comm, 2)
            grand_totals['total_company_net'] = round(grand_totals['total_company_net'] + o.company_net, 2)
            grand_totals['total_items'] += net_qty
            grand_totals['total_gross_items'] += order_qty
            grand_totals['total_shipping'] = round(grand_totals['total_shipping'] + (o.shipping_fee or 0), 2)
            grand_totals['total_ret_shipping'] = round(grand_totals['total_ret_shipping'] + o.ret_shipping_loss, 2)
            grand_totals['total_ret_missing'] = round(grand_totals['total_ret_missing'] + o.ret_missing_cost, 2)
            grand_totals['total_ret_deduction'] = round(grand_totals['total_ret_deduction'] + o.ret_total_deduction, 2)

    return render_template('invoices.html',
                           orders=orders,
                           is_gm=is_gm,
                           grand_totals=grand_totals,
                           is_proforma_view=show_proforma,
                           accessible_users=accessible_users_list, # قائمة الموظفين للفلترة
                           selected_user_id=int(selected_user_id) if selected_user_id and selected_user_id.isdigit() else None,
                           start_date=start_date,
                           end_date=end_date,
                           money_accounts=MoneyAccount.query.all())


@app.route('/api/invoice/<int:order_id>/add_payment', methods=['POST'])
@login_required
def add_invoice_payment(order_id):
    if current_user.fullname != "أحمد عبد الفتاح" and current_user.username != "gm_ahmed":
        return jsonify({'success': False, 'message': 'غير مصرح لك بإضافة مدفوعات للفاتورة.'})
    
    order = SaleOrder.query.get_or_404(order_id)
    amount = float(request.form.get('amount', 0))
    account_id = request.form.get('account_id')
    
    if amount <= 0:
        return jsonify({'success': False, 'message': 'المبلغ غير صالح.'})
        
    if not account_id:
        return jsonify({'success': False, 'message': 'يجب اختيار خزينة.'})
        
    account = MoneyAccount.query.get(account_id)
    if not account:
        return jsonify({'success': False, 'message': 'الخزينة غير صحيحة.'})
        
    # Update order paid upfront and amount due
    order.paid_upfront = round(order.paid_upfront + amount, 1)
    
    # Calculate amount due correctly based on current shipping fees
    fee = order.shipping_fee or 0.0
    order.amount_due = round(order.final_total + fee - order.paid_upfront, 1)
    
    # Update treasury balance
    account.balance = round(account.balance + amount, 1)
    
    # Record financial transaction
    transaction = FinancialTransaction(
        type='income',
        category='تحصيل مبيعات إضافي',
        amount=amount,
        description=f'تحصيل إضافي لفاتورة رقم #{order.id} من شركة الشحن',
        account_id=account.id,
        created_by_id=current_user.id,
        date=cairo_now()
    )
    db.session.add(transaction)
    
    db.session.commit()
    
    return jsonify({'success': True, 'message': f'تم إضافة الدفعة بنجاح. المتبقي للتحصيل: {order.amount_due}'})


@app.route('/api/invoice/<int:order_id>/payment_details')
@login_required
def invoice_payment_details(order_id):
    order = SaleOrder.query.get_or_404(order_id)
    payments = []
    
    # البحث عن الحركات المالية المرتبطة (بكلمة فاتورة أو رقم الفاتورة أو رقم البوليصة)
    waybill_filter = f"%{order.waybill_no}%" if order.waybill_no else "---"
    # نجلب كل الحركات اللي فيها الرقم كجزء من النص أولاً
    potential_trans = FinancialTransaction.query.filter(
        db.or_(
            FinancialTransaction.description.like(f"%#{order_id}%"),
            FinancialTransaction.description.like(waybill_filter)
        )
    ).all()
    
    # تصفية دقيقة باستخدام Regex في بايثون للتأكد من تطابق الرقم بالكامل
    import re
    trans = []
    for t in potential_trans:
        # 1. التحقق من رقم الفاتورة (يجب أن يكون مسبوقاً بـ # ومطابقاً تماماً)
        id_match = re.search(rf'#{order_id}(\b|[^\d])', t.description)
        
        # 2. التحقق من رقم البوليصة (لو موجود ومطابق تماماً)
        waybill_match = False
        if order.waybill_no:
            # لو رقم البوليصة قصير (أقل من 4 أرقام)، لازم نتأكد إنه مسبوق بكلمة 'بوليصة' 
            # وكمان نتأكد إن اسم العميل (أو جزء منه) موجود في الوصف لزيادة الدقة
            waybill_pattern = rf'(بوليصة|شحنة|باقة).*?{order.waybill_no}(\b|[^\d])'
            if len(str(order.waybill_no)) < 4:
                # التحقق من الرقم + اسم العميل (أول كلمة من الاسم كافية للفلترة)
                first_name = order.customer.name.split()[0] if order.customer and order.customer.name else ""
                has_waybill = re.search(waybill_pattern, t.description)
                has_customer = first_name in t.description if first_name else True
                waybill_match = has_waybill and has_customer
            else:
                waybill_match = re.search(waybill_pattern, t.description)
        
        if id_match or waybill_match:
            trans.append(t)
    
    # ترتيب الحركات حسب التاريخ
    trans.sort(key=lambda x: x.date)
    
    total_paid = 0
    for i, t in enumerate(trans, 1):
        if 'مرتجع' in t.description or 'استرداد' in t.description:
            p_type = 'استرداد'
        elif 'شحن' in t.description:
            p_type = 'تحصيل شحن'
        else:
            p_type = 'دفعة'
            
        payments.append({
            'index': i,
            'date': t.date.strftime('%Y-%m-%d %I:%M %p'),
            'amount': abs(t.amount),
            'account': t.account.name if t.account else ('حساب العميل' if t.type == 'debt_adjustment' else 'مجهول'),
            'type_label': p_type,
            'description': t.description,
            'is_in': t.amount > 0
        })
        if t.amount > 0:
            total_paid += t.amount
        else:
            total_paid -= abs(t.amount)
            
    # إضافة سطر لمصاريف الشحن (لو موجودة) عشان الحسبة تقفل في وش المستخدم
    if order.shipping_fee and order.shipping_fee > 0:
        payments.append({
            'index': len(payments) + 1,
            'date': order.shipping_settled_date.strftime('%Y-%m-%d %I:%M %p') if order.shipping_settled_date else '---',
            'amount': order.shipping_fee,
            'account': 'شركة الشحن (خصم)',
            'type_label': 'مصاريف شحن',
            'description': f'عمولة شركة الشحن (بوليصة {order.waybill_no})',
            'is_in': False # بنخليها باللون الأحمر كأنها مصروف
        })
        
    return jsonify({
        'invoice_id': order.id,
        'total_invoice': order.final_total,
        'total_paid': total_paid + (order.shipping_fee or 0),
        'amount_due': order.amount_due,
        'payments': payments
    })


@app.route('/api/invoice/<int:order_id>/commission_details')
@login_required
@general_manager_required
def invoice_commission_details(order_id):
    order = db.session.get(SaleOrder, order_id)
    if not order:
        return jsonify({'error': 'Invoice not found'}), 404

    order_qty = sum(item.quantity for item in order.items)
    seller = db.session.get(User, order.user_id) if order.user_id else None
    
    # بيانات المرتجعات
    ret_total_qty = sum(r.total_qty or 0 for r in order.return_invoices) if order.return_invoices else 0
    ret_total_deduction = sum(r.total_deduction or 0 for r in order.return_invoices) if order.return_invoices else 0
    ret_shipping_loss = sum(r.shipping_loss or 0 for r in order.return_invoices) if order.return_invoices else 0
    ret_missing_cost = sum(r.missing_items_cost or 0 for r in order.return_invoices) if order.return_invoices else 0
    net_qty = max(0, order_qty - ret_total_qty)

    details = {
        'order_id': order.id,
        'order_qty': order_qty,
        'ret_qty': ret_total_qty,
        'net_qty': net_qty,
        'total_amount': order.total_amount,
        'discount': order.discount or 0.0,
        'ret_total_deduction': ret_total_deduction,
        'is_under_partner': False,
        'seller_name': seller.fullname if seller else 'غير معروف',
        'est_comm': 0.0,
        'net_comm': 0.0,
        'calculation_method': '',
        'breakdown': []
    }

    if seller:
        is_under_partner = False
        if seller.role == 'manager': is_under_partner = True
        elif seller.manager_id:
            mgr = db.session.get(User, seller.manager_id)
            if mgr and mgr.role == 'manager': is_under_partner = True
        
        details['is_under_partner'] = is_under_partner

        if is_under_partner:
            partner_obj = seller if seller.role == 'manager' else db.session.get(User, seller.manager_id)
            p_rate = float(partner_obj.commission_value or 13.0) if partner_obj else 13.0
            details['calculation_method'] = 'عمولة شريك (المدير يتحمل الخصم من عمولته)'
            details['est_comm'] = net_qty * p_rate
            details['net_comm'] = details['est_comm'] - details['discount'] - ret_total_deduction
            details['breakdown'].append({'text': f'عدد القطع الإجمالي: {order_qty}', 'color': 'secondary'})
            if ret_total_qty > 0:
                details['breakdown'].append({'text': f'مرتجع: -{ret_total_qty} قطعة → الصافي: {net_qty} قطعة', 'color': 'warning'})
            details['breakdown'].append({'text': f'عمولة الشريك: {net_qty} × {p_rate} = {details["est_comm"]} ج.م', 'color': 'success'})
            if details['discount'] > 0:
                details['breakdown'].append({'text': f'خصم الفاتورة (يُطرح من عمولة المدير): -{details["discount"]} ج.م', 'color': 'danger'})
            if ret_shipping_loss > 0:
                details['breakdown'].append({'text': f'خسارة شحن مرتجع: -{ret_shipping_loss} ج.م', 'color': 'danger'})
            if ret_missing_cost > 0:
                details['breakdown'].append({'text': f'تكلفة نواقص/تالف: -{ret_missing_cost} ج.م', 'color': 'danger'})
            details['breakdown'].append({'text': f'صافي العمولة النهائي: {details["net_comm"]} ج.م', 'color': 'dark'})
        else:
            details['calculation_method'] = 'عمولة مباشرة (الشركة تتحمل الخصم)'
            def get_monthly_items(uid, o_date):
                start_date = o_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                if start_date.month == 12: end_date = start_date.replace(year=start_date.year + 1, month=1)
                else: end_date = start_date.replace(month=start_date.month + 1)
                gross = db.session.query(func.sum(SaleItem.quantity)).join(SaleOrder).filter(
                    SaleOrder.user_id == uid, SaleOrder.date >= start_date, SaleOrder.date < end_date
                ).scalar() or 0
                ret = db.session.query(func.sum(ReturnInvoice.total_qty))\
                    .join(SaleOrder)\
                    .filter(SaleOrder.user_id == uid,
                            ReturnInvoice.date >= start_date,
                            ReturnInvoice.date < end_date).scalar() or 0
                return max(0, gross - ret)

            monthly_net = get_monthly_items(seller.id, order.date)
            details['est_comm'] = calculate_user_commission(seller, net_qty, monthly_net)
            details['net_comm'] = details['est_comm']
            details['breakdown'].append({'text': f'إجمالي مبيعات البائعة هذا الشهر: {monthly_net} قطعة', 'color': 'secondary'})
            if ret_total_qty > 0:
                details['breakdown'].append({'text': f'مرتجع من هذه الفاتورة: -{ret_total_qty} قطعة (الصافي: {net_qty})', 'color': 'warning'})
            details['breakdown'].append({'text': f'بناءً على الشريحة المستحقة: العمولة = {details["est_comm"]} ج.م', 'color': 'success'})
            if details['discount'] > 0:
                details['breakdown'].append({'text': f'تنبيه: الخصم ({details["discount"]} ج.م) تتحمله الشركة ولا يؤثر على عمولة البائعة.', 'color': 'info'})
    
    return jsonify(details)

