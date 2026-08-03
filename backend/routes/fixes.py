from sqlalchemy import cast, Date
"""
Routes: fixes
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

@app.route('/fixes/recalc-suppliers')
@general_manager_required
def recalc_suppliers():
    from flask import session
    current_season = session.get('season', 'شتوي 2027')
    
    suppliers = Supplier.query.all()
    count = 0
    for supp in suppliers:
        total_purchases = db.session.query(func.sum(PurchaseOrder.total_cost))\
            .filter(PurchaseOrder.supplier_id == supp.id, PurchaseOrder.season == current_season).scalar() or 0
            
        total_payments = db.session.query(func.sum(SupplierPayment.amount))\
            .filter(SupplierPayment.supplier_id == supp.id, SupplierPayment.season == current_season).scalar() or 0
        
        # Calculate returns since they are not stored in a PurchaseReturn table (assume returns for current season are after Aug 1 2026)
        if 'شتوي' in current_season:
            returns = StockMovement.query.filter(StockMovement.reason.like(f"%مرتجع شراء للمورد: {supp.name}%"), StockMovement.timestamp >= datetime(2026, 8, 1)).all()
        else:
            returns = StockMovement.query.filter(StockMovement.reason.like(f"%مرتجع شراء للمورد: {supp.name}%"), StockMovement.timestamp < datetime(2026, 8, 1)).all()
            
        total_returns = sum(abs(r.quantity_change) * (r.variant.cost_price if r.variant else 0) for r in returns)
        
        # Balance = Total Purchases - Total Payments - Total Returns
        supp.balance = total_purchases - total_payments - total_returns
        count += 1
    db.session.commit()
    flash(f'تم تصفية حسابات {count} موردين لتبدأ على نظيف في موسم ({current_season})', 'success')
    return redirect(url_for('suppliers'))

@app.route('/fixes/move-august-to-winter')
@general_manager_required
def move_august_to_winter():
    target_season = 'شتوي 2027'
    cutoff = datetime(2026, 8, 1)
    
    models = [
        Expense, PurchaseOrder, SupplierPayment, CustomerPayment,
        SaleOrder, ReturnInvoice, FinancialTransaction, PartnerTransaction
    ]
    
    total_moved = 0
    for model in models:
        records = model.query.filter(model.date >= cutoff).all()
        for r in records:
            r.season = target_season
            total_moved += 1
            
    db.session.commit()
    flash(f'تم نقل عدد {total_moved} معاملة (منذ 1/8) إلى الموسم الشتوي بنجاح', 'success')
    return redirect(url_for('dashboard'))

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
        # Note: Do not rollback here because it cancels the entire transaction.



@app.route('/fix_shifts')
@login_required
def fix_shifts():
    if current_user.role != 'general_manager':
        return 'غير مسموح', 403
    count = 0
    users = User.query.all()
    for u in users:
        changed = False
        if u.shift_start == '01:00':
            u.shift_start = '13:00'
            changed = True
        if u.shift_end == '05:00':
            u.shift_end = '17:00'
            changed = True
        if not u.shift_start or u.shift_start == '09:00':
            u.shift_start = '13:00'
            changed = True
        if changed:
            count += 1
    db.session.commit()
    return f'تم تعديل مواعيد الوردية لـ {count} موظف ✅ (13:00 - 17:00)'


@app.route('/fix_commissions')
@general_manager_required
def fix_commissions_manual():
    # بنجيب كل الموظفين السيلز
    sales_reps = User.query.filter_by(role='sales').all()
    count = 0
    today = cairo_now()

    for rep in sales_reps:
        # تحديث عمولاتهم للشهر الحالي
        update_monthly_commissions(rep.id, today)
        count += 1

    # تحديث عمولات المديرين برضه (لو باعوا بنفسهم)
    managers = User.query.filter_by(role='manager').all()
    for mgr in managers:
        update_monthly_commissions(mgr.id, today)
        count += 1

    return f"تم تحديث العمولات لـ {count} موظف ومدير بنجاح! راجع تقرير الشركاء الآن."

@app.route('/fix/transfer_sales')
@general_manager_required
def transfer_sales():
    from_username = request.args.get('from_user') # يوزر المدير (الخاطئ)
    to_username = request.args.get('to_user')     # يوزر الموظفة (الصحيح)

    if not from_username or not to_username:
        return "يجب تحديد from_user و to_user في الرابط"

    u_from = User.query.filter_by(username=from_username).first()
    u_to = User.query.filter_by(username=to_username).first()

    if not u_from or not u_to: return "مستخدم غير موجود"

    # نقل فواتير اليوم فقط (عشان منبوظش القديم)
    today = date.today()
    orders = SaleOrder.query.filter(
        SaleOrder.user_id == u_from.id,
        cast(SaleOrder.date, Date) == today
    ).all()

    count = 0
    for o in orders:
        o.user_id = u_to.id
        o.sales_rep_code = u_to.emp_code
        count += 1

    # إعادة حساب العمولات للموظفة
    update_monthly_commissions(u_to.id, cairo_now())

    db.session.commit()
    return f"تم نقل {count} فاتورة من {u_from.fullname} إلى {u_to.fullname} بنجاح! راجع بروفايلها الآن."

@app.route('/fix/cleanup_orphaned_transactions')
@general_manager_required
def cleanup_orphaned_transactions():
    try:
        # 1. نجيب كل حركات الـ HR اللي فيها سيرة فواتير
        hr_transactions = HRTransaction.query.filter(
            HRTransaction.note.like('%فاتورة #%')
        ).all()

        deleted_count = 0

        for trans in hr_transactions:
            # نستخرج رقم الفاتورة من الملاحظة
            # الملاحظة بتكون: "تسجيل مرتجع إداري: فاتورة #105 ..."
            match = re.search(r'فاتورة #(\d+)', trans.note)
            if match:
                order_id = int(match.group(1))

                # نبحث عن الفاتورة في السيستم
                order = SaleOrder.query.get(order_id)

                # لو الفاتورة مش موجودة (اتمسحت) -> يبقى الحركة دي "يتيمة" ولازم تتمسح
                if not order:
                    db.session.delete(trans)
                    deleted_count += 1

        db.session.commit()
        return f"""
        <div style="text-align:center; padding:50px;">
            <h1 style="color:green;">✅ تم تنظيف السجلات بنجاح!</h1>
            <h3>تم حذف {deleted_count} حركة معلقة لفواتير محذوفة.</h3>
            <p>الآن ستعود حسابات الموظفين دقيقة 100%.</p>
            <a href="/dashboard">عودة للرئيسية</a>
        </div>
        """

    except Exception as e:
        return f"حدث خطأ: {e}"

@app.route('/fix/update_db_schema')
def update_db_schema():
    try:

        inspector = inspect(db.engine)

        # 1. التأكد من وجود جدول supplier_payment
        if 'supplier_payment' in inspector.get_table_names():
            cols = [c['name'] for c in inspector.get_columns('supplier_payment')]

            # 2. لو عمود account_id مش موجود، نضيفه
            if 'account_id' not in cols:
                with db.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE supplier_payment ADD COLUMN account_id INTEGER REFERENCES money_account(id)"))
                    conn.commit()
                return "✅ تم تحديث جدول الموردين وإضافة عمود الخزينة بنجاح!"
            else:
                return "⚠️ العمود موجود بالفعل، لا داعي للتحديث."
        else:
            return "❌ جدول supplier_payment غير موجود أصلاً!"
            
        # 3. تحديث جدول المرتجعات بإضافة عمود الكمية
        if 'return_invoice' in inspector.get_table_names():
            cols = [c['name'] for c in inspector.get_columns('return_invoice')]
            if 'total_qty' not in cols:
                with db.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE return_invoice ADD COLUMN total_qty INTEGER DEFAULT 0"))
                    conn.commit()
                return "✅ تم تحديث جدول المرتجعات وإضافة عمود الكمية بنجاح!"

    except Exception as e:
        return f"حدث خطأ: {e}"
# --- إضافة دالة تعديل العميل ---

@app.route('/fix/recalculate_treasury')
@login_required
def recalculate_treasury():
    # التأكد من الصلاحية
    if current_user.role != 'general_manager':
        return "غير مصرح لك", 403

    try:
        accounts = MoneyAccount.query.all()
        updated_log = []
        total_diff = 0.0
        report_content = "تقرير التدقيق المالي ومطابقة الأرصدة المتضررة\n\n"

        for acc in accounts:
            # 1. تجميع كل الحركات المرتبطة بهذا الحساب (سواء بالـ ID أو بالاسم للنظام القديم)
            # نجمع القيم: الإيداع بيكون موجب، والمصروف بيكون سالب في الداتا بيز
            real_balance = db.session.query(func.sum(FinancialTransaction.amount))\
                .filter(or_(
                    FinancialTransaction.account_id == acc.id,
                    ((FinancialTransaction.account_id == None) & 
                     FinancialTransaction.description.like(f"%{acc.name}%"))
                )).scalar() or 0.0
            
            real_balance = round(real_balance, 2)
            old_balance = round(acc.balance, 2)

            if abs(real_balance - old_balance) > 0.01:
                diff = real_balance - old_balance
                total_diff += diff
                
                # إظهار التغيير بلون يعبر عن الزيادة أو النقصان
                if diff > 0:
                    updated_log.append(f"<div style='color:green; margin-bottom:5px;'>✅ <b>{acc.name}:</b> كان ({old_balance}) أصبح ({real_balance}) ➔ بزيادة قدرها: {round(diff, 2)} ج.م</div>")
                else:
                    updated_log.append(f"<div style='color:red; margin-bottom:5px;'>🔻 <b>{acc.name}:</b> كان ({old_balance}) أصبح ({real_balance}) ➔ بعجز قدره: {round(abs(diff), 2)} ج.م</div>")

                # جلب كل الحركات الخاصة بالخزنة لكتابتها في التقرير
                txs = FinancialTransaction.query.filter(or_(
                    FinancialTransaction.account_id == acc.id,
                    ((FinancialTransaction.account_id == None) & 
                     FinancialTransaction.description.like(f"%{acc.name}%"))
                )).order_by(FinancialTransaction.date.desc()).all()

                report_content += f"=========================================\n"
                report_content += f"الخزنة: {acc.name}\n"
                report_content += f"الرصيد القديم (خاطئ): {old_balance} ج.م\n"
                report_content += f"الرصيد الفعلي (مصحح): {real_balance} ج.م\n"
                report_content += f"قيمة الفرق: {round(diff, 2)} ج.م\n"
                report_content += f"=========================================\n"
                report_content += f"سجل العمليات الذي تم الاحتساب بناءً عليه:\n"
                report_content += f"{'التاريخ'.ljust(20)} | {'المبلغ'.ljust(10)} | {'النوع'.ljust(15)} | البيان\n"
                report_content += "-" * 80 + "\n"
                for tx in txs:
                    report_content += f"{tx.date.strftime('%Y-%m-%d %H:%M:%S').ljust(20)} | {str(tx.amount).ljust(10)} | {tx.type.ljust(15)} | {tx.description}\n"
                report_content += "\n\n"

                # 2. تحديث الرصيد بالقيمة الحقيقية
                acc.balance = real_balance

        db.session.commit()

        if not updated_log:
            log_html = "<div style='color:blue; font-weight:bold; text-align:center;'>🎉 لم يتم رصد أي تغييرات، جميع الأرصدة متطابقة ومضبوطة 100%</div>"
            total_html = ""
        else:
            log_html = "".join(updated_log)
            total_color = "green" if total_diff >= 0 else "red"
            total_text = "فائض" if total_diff >= 0 else "عجز"
            total_html = f"""
            <div style="margin-top:20px; padding-top:15px; border-top:2px dashed #ccc; text-align:center; font-size:18px;">
                <b>صافي التغييرات الكلية (لكل الخزائن):</b> 
                <span style="color:{total_color}; font-weight:bold; font-size:22px;">{round(abs(total_diff), 2)} ج.م ({total_text})</span>
            </div>
            """
            
            # حفظ التقرير في ملف
            import os
            reports_dir = os.path.join(app.root_path, 'static', 'reports')
            os.makedirs(reports_dir, exist_ok=True)
            report_filename = f"Treasury_Audit_{cairo_now().strftime('%Y-%m-%d_%H-%M-%S')}.txt"
            report_path = os.path.join(reports_dir, report_filename)
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write(report_content)
                
            total_html += f"""
            <div style="margin-top:20px; text-align:center;">
                <p style="color:#555; font-size:14px; margin-bottom:10px;">تم حفظ نسخة من التقرير تلقائياً في خوادم النظام (<b>السيرفر</b>) للرجوع إليها لاحقاً.</p>
                <a href="/static/reports/{report_filename}" download class="btn btn-warning fw-bold shadow-sm" style="padding:10px 20px; text-decoration:none; border-radius:5px; color:#000;">
                    <i class="fas fa-download"></i> تحميل التقرير لجهازك (Download)
                </a>
            </div>
            """

        # تجهيز جدول الحركات المالية للعمود الأيمن
        all_txs_html = ""
        if updated_log:
            all_txs_html = """
            <div class="table-responsive" style="max-height: 500px; overflow-y: auto;">
                <table class="table table-sm table-hover" style="font-size: 13px;">
                    <thead class="table-dark" style="position: sticky; top: 0;">
                        <tr>
                            <th>التاريخ</th>
                            <th>الخزنة</th>
                            <th>المبلغ</th>
                            <th>البيان</th>
                        </tr>
                    </thead>
                    <tbody>
            """
            # جلب آخر 100 حركة للخزائن التي تأثرت فقط لعدم إثقال الصفحة
            affected_acc_ids = [acc.id for acc in accounts if any(acc.name in log for log in updated_log)]
            recent_txs = FinancialTransaction.query.filter(FinancialTransaction.account_id.in_(affected_acc_ids))\
                .order_by(FinancialTransaction.date.desc()).limit(100).all()
            
            for tx in recent_txs:
                # تحويل رقم الفاتورة لرابط لو وجد في الوصف
                import re
                desc = tx.description or ""
                order_match = re.search(r'#(\d+)', desc)
                if order_match:
                    order_id = order_match.group(1)
                    desc = desc.replace(f"#{order_id}", f'<a href="/invoice/print/{order_id}" target="_blank" class="badge bg-primary text-decoration-none">#{order_id} <i class="fas fa-external-link-alt"></i></a>')
                
                amount_class = "text-success" if tx.amount > 0 else "text-danger"
                all_txs_html += f"""
                <tr>
                    <td class="text-muted">{tx.date.strftime('%m-%d %H:%M')}</td>
                    <td class="fw-bold">{tx.account.name if tx.account else 'عام'}</td>
                    <td class="{amount_class} fw-bold">{tx.amount}</td>
                    <td>{desc}</td>
                </tr>
                """
            all_txs_html += "</tbody></table></div>"

        log_html = "".join(updated_log) if updated_log else "<div class='alert alert-info'>🎉 لا توجد فروقات مرصودة</div>"

        return f"""
        <!DOCTYPE html>
        <html lang="ar" dir="rtl">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>تقرير مطابقة الخزائن</title>
            <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
            <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
            <style>
                body {{ background: #f4f7f6; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }}
                .report-card {{ background: white; border-radius: 15px; box-shadow: 0 10px 30px rgba(0,0,0,0.1); border: none; }}
                .column-title {{ border-bottom: 2px solid #eee; padding-bottom: 10px; margin-bottom: 20px; font-weight: bold; color: #2c3e50; }}
                .diff-line {{ padding: 10px; border-radius: 8px; margin-bottom: 8px; background: #fff; border: 1px solid #eee; }}
            </style>
        </head>
        <body class="p-4">
            <div class="container-fluid">
                <div class="text-center mb-4">
                    <h1 class="display-5 fw-bold text-success"><i class="fas fa-check-double"></i> تم مطابقة أرصدة الخزائن</h1>
                    <p class="text-muted">تم مراجعة سجل العمليات وتصحيح الأرصدة بناءً على الحركات الفعلية.</p>
                </div>

                <div class="row g-4">
                    <!-- العمود الأيمن: سجل المعاملات المسببة -->
                    <div class="col-lg-7">
                        <div class="card report-card p-4">
                            <h4 class="column-title"><i class="fas fa-list-ul text-primary"></i> آخر المعاملات التي تمت مراجعتها</h4>
                            {all_txs_html if all_txs_html else '<p class="text-center py-5">لا توجد معاملات حديثة للخزائن المتأثرة</p>'}
                        </div>
                    </div>

                    <!-- العمود الأيسر: ملخص التعديلات -->
                    <div class="col-lg-5">
                        <div class="card report-card p-4">
                            <h4 class="column-title"><i class="fas fa-calculator text-warning"></i> ملخص تعديلات الأرصدة</h4>
                            <div class="mb-3">
                                {log_html}
                            </div>
                            <div class="bg-light p-3 rounded-3">
                                {total_html}
                            </div>
                            
                            <div class="d-grid gap-2 mt-4">
                                <a href="/treasury" class="btn btn-primary btn-lg shadow">
                                    <i class="fas fa-arrow-right me-2"></i> عودة لصفحة الخزينة
                                </a>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
        </body>
        </html>
        """

    except Exception as e:
        return f"حدث خطأ: {e}"

@app.route('/fix/force_reset_settings')
def force_reset_settings():
    try:
        # 1. حذف جدول الإعدادات القديم (لضمان نظافة العمل)
        # checkfirst=True تعني: احذفه فقط لو كان موجوداً
        SystemSetting.__table__.drop(db.engine, checkfirst=True)

        # 2. إنشاء الجدول من جديد بالأعمدة الصحيحة
        SystemSetting.__table__.create(db.engine)

        # 3. إضافة البيانات الافتراضية
        default_setting = SystemSetting()
        db.session.add(default_setting)
        db.session.commit()

        return """
        <h2 style='color:green; text-align:center; margin-top:50px;'>
            ✅ تم إعادة بناء جدول الإعدادات بنجاح!
            <br><br>
            <a href='/settings'>اذهب لصفحة الإعدادات الآن</a>
        </h2>
        """
    except Exception as e:
        return f"❌ حدث خطأ: {str(e)}"

@app.route('/fix/adjust_old_dates')
@general_manager_required
def adjust_old_dates():
    try:
        # مقدار الوقت المراد إضافته (مثلاً ساعتين)
        # لو التوقيت صيفي ومحتاج تزود 3 ساعات، خليها hours=3
        offset = timedelta(hours=2)

        count = 0

        # 1. تحديث الفواتير
        for r in SaleOrder.query.all():
            if r.date:
                r.date += offset
                count += 1

        # 2. تحديث الحركات المالية
        for r in FinancialTransaction.query.all():
            if r.date:
                r.date += offset
                count += 1

        # 3. تحديث حركات المخزون
        for r in StockMovement.query.all():
            if r.timestamp:
                r.timestamp += offset
                count += 1

        # 4. تحديث حركات الموظفين والرواتب
        for r in HRTransaction.query.all():
            if r.date:
                r.date += offset
                count += 1

        # 5. تحديث المصروفات
        for r in Expense.query.all():
            if r.date:
                r.date += offset
                count += 1

        # 6. تحديث حركات الشركاء
        for r in PartnerTransaction.query.all():
            if r.date:
                r.date += offset
                count += 1

        # 7. تحديث الحضور والانصراف
        for r in Attendance.query.all():
            if r.check_in:
                r.check_in += offset
            if r.check_out:
                r.check_out += offset
            count += 1

        db.session.commit()

        return f"""
        <div style="text-align:center; padding:50px;">
            <h1 style="color:green;">✅ تم تعديل التواريخ بنجاح!</h1>
            <h3>تم تحديث {count} سجل وإضافة ساعتين للوقت.</h3>
            <a href="/dashboard">عودة للرئيسية</a>
        </div>
        """

    except Exception as e:
        db.session.rollback()
        return f"حدث خطأ: {e}"
# === كود إصلاح الشحنات القديمة (شغله مرة واحدة) ===

@app.route('/fix/restore_shipping_orders')
@login_required
def restore_shipping_orders():
    if current_user.role != 'general_manager':
        return "غير مصرح لك"

    # البحث عن كل الشحنات التي تم إنهاؤها (settled)
    # سنعيدها إلى حالة (delivered) لكي تظهر لك مجدداً وتقوم بتحصيلها
    orders = SaleOrder.query.filter_by(is_shipping=True, shipping_status='settled').all()

    count = 0
    for o in orders:
        o.shipping_status = 'delivered'
        count += 1

    db.session.commit()

    return f"""
    <div style="text-align:center; padding:50px; font-family: tahoma;">
        <h1 style="color:green;">✅ تم استرجاع {count} شحنة بنجاح!</h1>
        <h3>تمت إعادة الشحنات المنتهية إلى حالة "تم التوصيل".</h3>
        <p>الآن ستجدها ظهرت في صفحة "متابعة الشحن".</p>
        <p><strong>المطلوب منك:</strong> اضغط على زر "تحصيل وإيداع" لكل واحدة لاختيار الخزينة وإدخال الأموال.</p>
        <br>
        <a href="/shipping/orders" style="background:#0d6efd; color:white; padding:10px 20px; text-decoration:none; border-radius:5px;">الذهاب لصفحة الشحن</a>
    </div>
    """
# === كود طباعة كتالوج المنتجات ===

@app.route('/fix/correct_settled_invoices')
@login_required
def correct_settled_invoices():
    if current_user.role != 'general_manager': return "غير مصرح"

    # البحث عن الفواتير التي حالتها "تم التحصيل" ولكن عليها مديونية
    orders = SaleOrder.query.filter(
        SaleOrder.shipping_status == 'settled',
        SaleOrder.amount_due > 0
    ).all()

    count = 0
    for o in orders:
        o.amount_due = 0 # تصفير المديونية
        count += 1

    db.session.commit()
    return f"تم تصحيح {count} فاتورة محصلة لتظهر كـ 'خالص'."
# === كود كشف العجز (Audit) ===

@app.route('/fix/create_excuse_table')
@general_manager_required
def create_excuse_table():
    try:
        inspector = inspect(db.engine)
        if 'employee_excuse' not in inspector.get_table_names():
            EmployeeExcuse.__table__.create(db.engine)
            return "✅ تم إنشاء جدول الأذونات (EmployeeExcuse) بنجاح!"
        else:
            return "⚠️ الجدول موجود بالفعل في قاعدة البيانات."
    except Exception as e:
        return f"❌ حدث خطأ: {str(e)}"
# === أداة إصلاح وتعديل مرتجعات الشراء ===

@app.route('/fix/purchase_returns_list')
@permission_required('manage_inventory')
def fix_purchase_returns_list():
    # جلب آخر 10 حركات مرتجع شراء
    moves = StockMovement.query.filter(
        StockMovement.reason.like('مرتجع شراء%')
    ).order_by(StockMovement.id.desc()).limit(10).all()

    # تصميم بسيط للعرض
    html = """
    <html dir="rtl">
    <head>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    </head>
    <body class="p-5 bg-light">
        <div class="container">
            <h2 class="mb-4 text-danger">🛠️ إصلاح مرتجعات الشراء (حذف المكرر)</h2>
            <div class="card shadow">
                <div class="card-body">
                    <table class="table table-bordered text-center">
                        <thead class="table-dark">
                            <tr>
                                <th>م</th>
                                <th>اسم الصنف</th>
                                <th>الكمية المرجعة</th>
                                <th>السبب (اسم المورد)</th>
                                <th>التاريخ</th>
                                <th>إجراء</th>
                            </tr>
                        </thead>
                        <tbody>
    """

    for m in moves:
        html += f"""
                            <tr>
                                <td>{m.id}</td>
                                <td>{m.variant.model.name if m.variant else 'محذوف'}</td>
                                <td class="fw-bold text-danger">{m.quantity_change}</td>
                                <td>{m.reason}</td>
                                <td>{m.timestamp.strftime('%Y-%m-%d %H:%M')}</td>
                                <td>
                                    <a href="/fix/delete_duplicate_return/{m.id}" class="btn btn-danger btn-sm" onclick="return confirm('هل أنت متأكد؟ سيتم استرجاع الكمية للمخزن وإعادة المديونية للمورد.')">
                                        <i class="fas fa-trash"></i> حذف التكرار
                                    </a>
                                </td>
                            </tr>
        """

    html += """
                        </tbody>
                    </table>
                    <a href="/dashboard" class="btn btn-secondary mt-3">عودة للرئيسية</a>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return html


@app.route('/fix/delete_duplicate_return/<int:id>')
@permission_required('manage_inventory')
def delete_duplicate_return(id):
    try:
        move = StockMovement.query.get_or_404(id)

        # 1. استرجاع الكمية للمخزن (عكس الحركة)
        variant = ProductVariant.query.get(move.variant_id)
        qty_to_restore = abs(move.quantity_change) # الكمية بالموجب

        if variant:
            variant.stock += qty_to_restore

        # 2. استرجاع المديونية للمورد (محاولة معرفة المورد من الوصف)
        # الوصف بيكون: "مرتجع شراء للمورد: فلان"
        msg_extra = ""
        try:
            if ":" in move.reason:
                supp_name = move.reason.split(':')[1].strip()
                supplier = Supplier.query.filter_by(name=supp_name).first()
                if supplier:
                    # حساب القيمة التقريبية (الكمية * التكلفة الحالية)
                    # لأننا مش مسجلين السعر وقت المرتجع في جدول الحركة، هناخد السعر الحالي
                    cost_val = qty_to_restore * (variant.cost_price or 0)
                    supplier.balance += cost_val # بنزود حسابه تاني (لأننا لغينا المرتجع)
                    msg_extra = f"وتم إعادة {cost_val} ج.م لحساب المورد {supplier.name}."
        except Exception as e:
            msg_extra = "ولكن لم نتمكن من تعديل رصيد المورد تلقائياً، يرجى مراجعته يدوياً."

        # 3. حذف الحركة الخطأ
        db.session.delete(move)
        db.session.commit()

        return f"""
        <h2 style='color:green; text-align:center; margin-top:50px;'>
            ✅ تم حذف المرتجع المكرر بنجاح!
            <br>
            <small>تمت إعادة {qty_to_restore} قطعة للمخزن. {msg_extra}</small>
            <br><br>
            <a href='/fix/purchase_returns_list'>عودة للقائمة</a>
        </h2>
        """

    except Exception as e:
        return f"حدث خطأ: {e}"

with app.app_context():
    db.create_all()


@app.route('/fix_attendance_420')
@login_required
def fix_attendance_420():
    if current_user.role != 'general_manager':
        return "غير مصرح", 403
    
    deleted = Attendance.query.filter(
        Attendance.user_id.in_([14, 15, 17]),
        Attendance.check_in.like('%09:00:%'),
        Attendance.check_out.like('%17:00:%')
    ).delete(synchronize_session=False)
    
    flex_users = User.query.filter_by(has_flexible_hours=True).all()
    if flex_users:
        flex_ids = [u.id for u in flex_users]
        deleted_flex = Attendance.query.filter(
            Attendance.user_id.in_(flex_ids),
            Attendance.status == 'absent'
        ).delete(synchronize_session=False)
    else:
        deleted_flex = 0
        
    db.session.commit()
    
    return f"تم تنظيف قاعدة البيانات الأونلاين بنجاح! تم حذف ({deleted}) سجل مضروب من 9 ل 5 للمشتركين.. وتم مسح ({deleted_flex}) عطل غياب للموظفين المرنين."

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')


