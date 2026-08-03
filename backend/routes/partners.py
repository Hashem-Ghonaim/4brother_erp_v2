"""
Routes: partners
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


@app.route('/partners/report')
@login_required
def partners_report():
    if current_user.role not in ['general_manager', 'manager']:
        return "غير مصرح", 403
    partners = User.query.filter_by(role='manager').all()
    report_data = []

    today = date.today()
    start_date_str = request.args.get('start_date', today.replace(day=1).strftime('%Y-%m-%d'))
    end_date_str = request.args.get('end_date', today.strftime('%Y-%m-%d'))

    grand_total_period = 0

    for p in partners:
        # 1. الحساب الحقيقي والمباشر للرصيد (الجمع الجبري لكل الحركات)
        # هذا هو الرقم النهائي الذي يحدد هل هو له أم عليه أم خالص
        current_balance = db.session.query(func.sum(PartnerTransaction.amount)).filter_by(partner_id=p.id).scalar() or 0.0

        def safe_float(val):
            if val is None: return 0.0
            if isinstance(val, str):
                return float(val.replace(',', ''))
            return float(val)

        # 2. تفصيل المبالغ للعرض فقط
        all_trans = PartnerTransaction.query.filter_by(partner_id=p.id).all()
        # إجمالي الأرباح (كل ما هو ليس سحب)
        total_earned = sum(safe_float(t.amount) for t in all_trans if t.type != 'withdrawal')
        # إجمالي المسحوبات (كل ما هو سحب) - نحسبه كصافي مسحوبات
        total_withdrawn = sum(safe_float(t.amount) for t in all_trans if t.type == 'withdrawal')



        start_datetime = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_datetime = datetime.strptime(end_date_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59)

        # 3. حساب أرقام الفترة (للتحليل المالي)
        # أ) تحديد فريق العمل (المدير + كل اللي تحته)
        team_ids = [p.id] + [u.id for u in User.query.filter_by(manager_id=p.id).all()]
        
        # ب) حساب المبيعات والمرتجعات المباشرة
        total_sold = db.session.query(func.sum(SaleItem.quantity)).join(SaleOrder).filter(
            SaleOrder.user_id.in_(team_ids),
            SaleOrder.is_proforma == False,
            SaleOrder.date >= start_datetime,
            SaleOrder.date <= end_datetime
        ).scalar() or 0

        same_month_ret = db.session.query(func.sum(ReturnInvoice.total_qty)).join(SaleOrder).filter(
            SaleOrder.user_id.in_(team_ids),
            SaleOrder.date >= start_datetime,
            SaleOrder.date <= end_datetime,
            ReturnInvoice.date >= start_datetime,
            ReturnInvoice.date <= end_datetime
        ).scalar() or 0

        cross_month_ret = db.session.query(func.sum(ReturnInvoice.total_qty)).join(SaleOrder).filter(
            SaleOrder.user_id.in_(team_ids),
            SaleOrder.date < start_datetime,
            ReturnInvoice.date >= start_datetime,
            ReturnInvoice.date <= end_datetime
        ).scalar() or 0

        # تفاصيل مرتجعات الأشهر السابقة (للعمود مرتجعات)
        cross_month_return_invoices = ReturnInvoice.query.join(SaleOrder).filter(
            SaleOrder.user_id.in_(team_ids),
            SaleOrder.date < start_datetime,
            ReturnInvoice.date >= start_datetime,
            ReturnInvoice.date <= end_datetime
        ).all()
        cross_month_return_details = []
        cross_month_13_deduction = 0
        for ret in cross_month_return_invoices:
            order = SaleOrder.query.get(ret.order_id)
            seller_name = order.sales_rep.fullname if order and order.sales_rep else '---'
            gross_qty = sum(item.quantity for item in order.items) if order else 0
            ret_qty = ret.total_qty or 0
            amount = -(ret_qty * float(p.commission_value or 13.0))
            cross_month_13_deduction += amount
            cross_month_return_details.append({
                'amount': amount,
                'user_name': seller_name,
                'gross_qty': gross_qty,
                'returned_qty': ret_qty,
                'desc': f"مرتجع من شهر سابق - فاتورة #{ret.order_id} ({ret_qty} قطعة)",
                'date': ret.date.strftime('%Y-%m-%d'),
                'order_id': ret.order_id,
                'invoice_label': f"فاتورة #{ret.order_id}"
            })

        final_pieces = max(0, total_sold - same_month_ret)

        # قيمة عمولة الشريك (خام = إجمالي المبيعات × 13) مع تفاصيل الفواتير
        gross_comm_display = total_sold * float(p.commission_value or 13.0)
        team_orders = SaleOrder.query.filter(
            SaleOrder.user_id.in_(team_ids),
            SaleOrder.is_proforma == False,
            SaleOrder.date >= start_datetime,
            SaleOrder.date <= end_datetime
        ).order_by(SaleOrder.date).all()
        gross_comm_details_display = []
        for order in team_orders:
            gross_qty = sum(item.quantity for item in order.items)
            returned_qty = db.session.query(func.sum(ReturnInvoice.total_qty)).filter(
                ReturnInvoice.order_id == order.id,
                ReturnInvoice.date >= start_datetime,
                ReturnInvoice.date <= end_datetime
            ).scalar() or 0
            net_qty = max(0, gross_qty - returned_qty)
            seller_name = order.sales_rep.fullname if order.sales_rep else ''
            gross_comm_details_display.append({
                'qty': gross_qty,
                'amount': net_qty * float(p.commission_value or 13.0),
                'gross_qty': gross_qty,
                'returned_qty': returned_qty,
                'user_name': seller_name,
                'desc': f"فاتورة #{order.id} ({gross_qty} قطعة) - {seller_name}",
                'date': order.date.strftime('%Y-%m-%d'),
                'order_id': order.id,
                'invoice_label': f"فاتورة #{order.id}"
            })

        # إعادة حساب العرض ليطابق مجموع تفاصيل البوب أب (net × 13 بعد خصم المرتجعات)
        gross_comm_display = sum(item['amount'] for item in gross_comm_details_display)

        # ج) جلب حركات الفترة
        period_trans = PartnerTransaction.query.filter(
            PartnerTransaction.partner_id == p.id,
            PartnerTransaction.date >= start_datetime,
            PartnerTransaction.date <= end_datetime
        ).all()

        print(f"DEBUG {p.fullname}: start={start_datetime} end={end_datetime} num_trans={len(period_trans)}")

        # حساب عمولات السيلز المستردة للعرض — only cross-month (من HRTransaction return_reversal)
        # تم بيعها في أشهر سابقة وأرجعها العميل في الشهر الحالي
        sales_rep_comm_reversed_display = 0
        sales_reversed_comm_details = []

        hr_return_reversals = HRTransaction.query.filter(
            HRTransaction.user_id.in_(team_ids),
            HRTransaction.type == 'return_reversal',
            HRTransaction.date >= start_datetime,
            HRTransaction.date <= end_datetime
        ).all()

        for t in hr_return_reversals:
            employee = User.query.get(t.user_id) if t.user_id else None
            user_name = employee.fullname if employee else '---'
            inv_match = re.search(r'#(\d+)', t.note or '')
            order_id = int(inv_match.group(1)) if inv_match else None
            invoice_label = f"فاتورة #{order_id}" if order_id else "---"
            sales_reversed_comm_details.append({
                'amount': abs(t.amount),
                'user_name': user_name,
                'desc': t.note or '',
                'date': t.date.strftime('%Y-%m-%d'),
                'order_id': order_id,
                'invoice_label': invoice_label
            })
            sales_rep_comm_reversed_display += abs(t.amount)

        # تفاصيل عمولات مبيعات — ب break-down (عدد قطع الفاتورة, المرتجعات, القيمة النهائية)
        sales_comm_details_built = []
        for t in period_trans:
            if t.type == 'sub_commission' and safe_float(t.amount) < 0:
                desc = t.description or ''
                user_name = ''
                m = re.search(r'عمولة\s*\(([^)]+)\)', desc)
                if m:
                    user_name = m.group(1)
                if not user_name and t.order_id:
                    order = SaleOrder.query.get(t.order_id)
                    if order and order.sales_rep:
                        user_name = order.sales_rep.fullname

                gross_qty = 0
                returned_qty = 0
                if t.order_id:
                    order = SaleOrder.query.get(t.order_id)
                    if order:
                        gross_qty = sum(item.quantity for item in order.items)
                        target_month_str = t.date.strftime('%Y-%m')
                        returned_qty = sum(r.total_qty for r in order.return_invoices if r.date.strftime('%Y-%m') == target_month_str) if order.return_invoices else 0

                net_qty = max(0, gross_qty - returned_qty)
                rate = abs(t.amount) / net_qty if net_qty > 0 else 0

                sales_comm_details_built.append({
                    'amount': round(net_qty * rate, 2),
                    'user_name': user_name,
                    'gross_qty': gross_qty,
                    'returned_qty': returned_qty,
                    'desc': desc,
                    'date': t.date.strftime('%Y-%m-%d'),
                    'order_id': t.order_id,
                    'invoice_label': f"فاتورة #{t.order_id}" if t.order_id else "---"
                })

        gross_comm = sum(safe_float(t.amount) for t in period_trans if t.type == 'commission_gross' and safe_float(t.amount) > 0)
        
        # عمولات مبيعات (المدفوعة فقط، تم تجاهل المستردة بناء على طلب المستخدم)
        sales_rep_comm = sum(safe_float(t.amount) for t in period_trans if t.type == 'sub_commission' and safe_float(t.amount) < 0)
        sales_rep_comm_reversed = sum(safe_float(t.amount) for t in period_trans if t.type == 'sub_commission' and safe_float(t.amount) > 0)
        
        discounts = sum(safe_float(t.amount) for t in period_trans if t.type == 'discount_deduction')
        
        # المرتجعات تشمل: مرتجعات الأشهر السابقة (خصم 13ج) + تحمل خسائر الشحن
        return_penalty = sum(safe_float(t.amount) for t in period_trans if t.type == 'return_penalty')
        
        returns = cross_month_13_deduction + return_penalty
        
        expenses = sum(safe_float(t.amount) for t in period_trans if t.type == 'expense_share')
        staff_costs = sum(safe_float(t.amount) for t in period_trans if t.type == 'staff_expense')
        withdrawals_period = sum(safe_float(t.amount) for t in period_trans if t.type == 'withdrawal')

        # الفصول الجديدة بناءً على الإشارة (موجب/سالب) لتوضيح الإيرادات والخصومات
        admin_bonus_earned = sum(safe_float(t.amount) for t in period_trans if t.type == 'admin_bonus' and safe_float(t.amount) > 0)
        admin_bonus_paid = sum(safe_float(t.amount) for t in period_trans if t.type == 'admin_bonus' and safe_float(t.amount) <= 0)
        
        admin_penalty_recovered = sum(safe_float(t.amount) for t in period_trans if t.type == 'admin_penalty' and safe_float(t.amount) > 0)
        admin_penalty_deducted = sum(safe_float(t.amount) for t in period_trans if t.type == 'admin_penalty' and safe_float(t.amount) <= 0)

        print(f"DEBUG {p.fullname}: gross={gross_comm} sales_rep={sales_rep_comm}")

        # صافي ربح النشاط (بدون المسحوبات)
        period_net_profit = (gross_comm_display + admin_bonus_earned + admin_penalty_recovered + 
                             sales_rep_comm_reversed +
                             sales_rep_comm + discounts + returns + expenses + staff_costs + 
                             admin_bonus_paid + admin_penalty_deducted)
        
        # صافي حركة النقدية (الربح - المسحوبات)
        period_net_cash = period_net_profit + withdrawals_period

        grand_total_period += period_net_cash

        # Helper to build detail list from transactions
        def build_details(trans_list, custom_filter):
            filtered = [t for t in trans_list if custom_filter(t)]
                
            res = []
            for t in filtered:
                desc = t.description or ""
                share_type = "تحمل 100%"
                op_type = "مصروف طاقم"
                if "50%" in desc: share_type = "مشترك (50/50)"
                if "راتب" in desc or "مرتب" in desc: op_type = "راتب شهرى"
                
                user_name = ''
                if t.order_id:
                    order = SaleOrder.query.get(t.order_id)
                    if order and order.sales_rep:
                        user_name = order.sales_rep.fullname
                if not user_name:
                    m = re.search(r'عمولة\s*\(([^)]+)\)', desc)
                    if m:
                        user_name = m.group(1)
                
                res.append({
                    'amount': t.amount,
                    'user_name': user_name,
                    'desc': desc,
                    'share_type': share_type,
                    'op_type': op_type,
                    'date': t.date.strftime('%Y-%m-%d'),
                    'order_id': t.order_id,
                    'invoice_label': f"فاتورة #{t.order_id}" if t.order_id else "---"
                })
            return res

        report_data.append({
            'id': p.id,
            'name': p.fullname,
            'sold_items': final_pieces,
            'sold_details': {
                'total_sold': total_sold,
                'same_month_returns': same_month_ret,
                'cross_month_returns': cross_month_ret,
                'net_pieces': final_pieces
            },
            'gross_comm': round(gross_comm, 2),
            'gross_comm_display': round(gross_comm_display, 2),
            'gross_comm_details_display': gross_comm_details_display,
            'sales_rep_comm_reversed': round(sales_rep_comm_reversed, 2),
            'sales_rep_comm_reversed_display': round(sales_rep_comm_reversed_display, 2),
            'admin_bonus_earned': round(admin_bonus_earned, 2),
            'admin_penalty_recovered': round(admin_penalty_recovered, 2),
            'sales_rep_comm': round(sales_rep_comm, 2),
            'discounts': round(discounts, 2),
            'returns': round(returns, 2),
            'expenses': round(expenses, 2),
            'staff_costs': round(staff_costs, 2),
            'admin_bonus_paid': round(admin_bonus_paid, 2),
            'admin_penalty_deducted': round(admin_penalty_deducted, 2),
            
            # تفاصيل كل نوع للعرض في المودال
            'gross_comm_details': build_details(period_trans, lambda t: t.type == 'commission_gross' and safe_float(t.amount) > 0),
            'admin_bonus_earned_details': build_details(period_trans, lambda t: t.type == 'admin_bonus' and safe_float(t.amount) > 0),
            'admin_penalty_recovered_details': build_details(period_trans, lambda t: t.type == 'admin_penalty' and safe_float(t.amount) > 0),
            'sales_comm_reversed_details': sales_reversed_comm_details,
            'sales_comm_details': sales_comm_details_built,
            'discounts_details': build_details(period_trans, lambda t: t.type == 'discount_deduction'),
            'returns_details': cross_month_return_details + build_details(period_trans, lambda t: t.type == 'return_penalty'),
            'expenses_details': build_details(period_trans, lambda t: t.type == 'expense_share'),
            'staff_costs_details': build_details(period_trans, lambda t: t.type == 'staff_expense'),
            'admin_bonus_paid_details': build_details(period_trans, lambda t: t.type == 'admin_bonus' and safe_float(t.amount) <= 0),
            'admin_penalty_deducted_details': build_details(period_trans, lambda t: t.type == 'admin_penalty' and safe_float(t.amount) <= 0),
            'withdrawals_details': build_details(period_trans, lambda t: t.type == 'withdrawal'),

            'period_net_profit': round(period_net_profit, 2),
            'withdrawals_period': round(abs(withdrawals_period), 2),
            'period_net_cash': round(period_net_cash, 2),
            
            'total_earned': round(total_earned, 2),
            'total_withdrawn': round(abs(total_withdrawn), 2),
            'current_balance': round(current_balance, 2)
        })
    totals = {
        'sold_items': sum(r['sold_items'] for r in report_data),
        'gross_comm_display': round(sum(r['gross_comm_display'] for r in report_data), 2),
        'sales_rep_comm_reversed_display': round(sum(r['sales_rep_comm_reversed_display'] for r in report_data), 2),
        'admin_bonus_earned': round(sum(r['admin_bonus_earned'] for r in report_data), 2),
        'admin_penalty_recovered': round(sum(r['admin_penalty_recovered'] for r in report_data), 2),
        'sales_rep_comm': round(sum(r['sales_rep_comm'] for r in report_data), 2),
        'returns': round(sum(r['returns'] for r in report_data), 2),
        'admin_penalty_deducted': round(sum(r['admin_penalty_deducted'] for r in report_data), 2),
        'admin_bonus_paid': round(sum(r['admin_bonus_paid'] for r in report_data), 2),
        'discounts': round(sum(r['discounts'] for r in report_data), 2),
        'staff_costs': round(sum(r['staff_costs'] for r in report_data), 2),
        'expenses': round(sum(r['expenses'] for r in report_data), 2),
        'withdrawals_period': round(sum(r['withdrawals_period'] for r in report_data), 2),
        'period_net_cash': round(grand_total_period, 2),
    }
    accounts = MoneyAccount.query.all()
    return render_template('partners_report.html',
                           report=report_data,
                           grand_total=round(grand_total_period, 2),
                           totals=totals,
                           start_date=start_date_str,
                           end_date=end_date_str,
                           accounts=accounts)

# دالة صرف تصفية الأرباح (يجب إضافتها لكي يعمل زر الصرف)

@app.route('/partners/settle', methods=['POST'])
@general_manager_required
def partner_settlement():
    try:
        partner_id = request.form.get('partner_id')
        amount = float(request.form.get('amount') or 0)
        account_id = request.form.get('account_id')
        notes = request.form.get('notes', 'تصفية أرباح')

        if amount <= 0 or not account_id:
            flash('بيانات التصفية غير مكتملة أو المبلغ خطأ', 'danger')
            return redirect(url_for('partners_report'))

        account = MoneyAccount.query.get(account_id)
        if not account:
            flash('الخزينة غير موجودة', 'danger')
            return redirect(url_for('partners_report'))

        # 1. خصم من الخزينة
        account.balance = round(account.balance - amount, 1)

        # 2. تسجيل حركة مالية عامة في الخزينة
        db.session.add(FinancialTransaction(
            account_id=account.id,
            type='expense',
            category='تصفية شركاء',
            amount=-amount,
            description=f"صرف تصفية أرباح للشريك (ID:{partner_id})",
            created_by_id=current_user.id,
            date=cairo_now()
        ))

        # 3. تسجيل حركة "سحب" في حساب الشريك لخصمها من رصيده
        db.session.add(PartnerTransaction(
            partner_id=partner_id,
            type='withdrawal',
            amount=-amount,
            description=f"استلام تصفية: {notes}",
            date=cairo_now()
        ))

        db.session.commit()
        flash(f'تم تسجيل الصرف بنجاح وخصم {amount} ج.م من {account.name} ✅', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'حدث خطأ: {str(e)}', 'danger')

    return redirect(url_for('partners_report'))

@app.route('/partners/recalculate-commissions', methods=['POST'])
@general_manager_required
def recalculate_commissions():
    try:
        target_month_str = request.form.get('target_month')
        if not target_month_str:
            flash('يجب تحديد الشهر بصيغة YYYY-MM', 'danger')
            return redirect(url_for('partners_report'))

        ref_date = datetime.strptime(target_month_str + '-01', '%Y-%m-%d')

        from ..routes.invoices import update_monthly_commissions

        user_ids = db.session.query(SaleOrder.user_id).filter(
            SaleOrder.is_proforma == False,
            func.to_char(SaleOrder.date, 'YYYY-MM') == target_month_str
        ).distinct().all()

        processed = 0
        for (uid,) in user_ids:
            user = db.session.get(User, uid)
            if user and (user.manager_id or user.role == 'manager'):
                update_monthly_commissions(uid, ref_date)
                processed += 1

        flash(f'✅ تم إعادة حساب العمولات لـ {processed} مندوب/مدير عن شهر {target_month_str}', 'success')
    except Exception as e:
        flash(f'❌ حدث خطأ: {str(e)}', 'danger')

    return redirect(url_for('partners_report'))

@app.route('/migrate-unassigned-expenses')
@login_required
def migrate_unassigned_expenses():
    if current_user.role != 'general_manager':
        return "غير مصرح", 403

    gm = User.query.filter_by(role='general_manager').first()
    if not gm:
        return "لم يتم العثور على المدير العام", 404

    all_expenses = Expense.query.all()

    # جلب كل PartnerTransaction الموجودة
    all_partner_trans = PartnerTransaction.query.filter(
        PartnerTransaction.type.in_(['expense_share', 'staff_expense', 'withdrawal'])
    ).all()

    # إيجاد المصاريف اللي ملهاش حركة
    unassigned = []
    for exp in all_expenses:
        exp_date = exp.date.strftime('%Y-%m-%d') if exp.date else ''
        exp_amount = round(float(exp.amount or 0), 2)
        exp_desc = (exp.description or '').strip()

        found = False
        for t in all_partner_trans:
            t_date = t.date.strftime('%Y-%m-%d') if t.date else ''
            t_amt_str = str(t.amount or '0').replace(',', '')
            t_amount = round(abs(float(t_amt_str)), 2)
            t_desc = (t.description or '').strip()

            if t_date == exp_date and abs(t_amount - exp_amount) < 0.5:
                found = True; break
            if exp_desc and len(exp_desc) > 5 and exp_desc in t_desc:
                found = True; break

        if not found:
            unassigned.append(exp)

    confirm = request.args.get('confirm', '')

    if confirm == 'yes' and unassigned:
        created = 0
        for exp in unassigned:
            amt = float(exp.amount or 0)
            desc = exp.description or (exp.category.name if exp.category else 'مصروف')
            
            # تحديد حصة أحمد بناءً على الكلمات المفتاحية في الوصف فقط
            ahmed_share_ratio = 1.0  # الافتراضي 100%
            if 'شركاء' in desc:
                ahmed_share_ratio = 0.0  # مش على أحمد خالص
            elif 'مشترك' in desc or '50' in desc:
                ahmed_share_ratio = 0.5  # 50% بس على أحمد

            if ahmed_share_ratio > 0:
                final_amt = amt * ahmed_share_ratio
                ratio_text = "(حصة 50%)" if ahmed_share_ratio == 0.5 else "(تعيين بالكامل لعدم وجود حصة)"
                
                db.session.add(PartnerTransaction(
                    partner_id=gm.id,
                    type='expense_share',
                    amount=-final_amt,
                    description=f"{desc} {ratio_text}",
                    date=exp.date
                ))
                created += 1
        db.session.commit()
        return f"<h2 style='color:green'>✅ تم إنشاء {created} حركة expense_share على {gm.fullname}</h2><a href='/owner-settlement'>← رجوع للتصفية</a>"

    # عرض المصاريف الغير محددة (dry run)
    html = f"<html dir='rtl'><body style='font-family:sans-serif;padding:20px;background:#1a1d25;color:#eee'>"
    html += f"<h2>🔍 مصاريف غير محددة: {len(unassigned)} من أصل {len(all_expenses)}</h2>"
    html += f"<p>سيتم تعيينها على <b>{gm.fullname}</b> كالتالي:</p>"
    html += "<ul><li>الوصف يحتوي على 'مشترك' أو '50' → <b>50% فقط</b></li>"
    html += "<li>الوصف يحتوي على 'شركاء' → <b>يتم تجاهله (0%)</b></li>"
    html += "<li>غير ذلك → <b>100%</b></li></ul><br>"

    if unassigned:
        html += "<table border='1' cellpadding='8' style='border-collapse:collapse;color:#eee;width:100%'>"
        html += "<tr style='background:#333'><th>#</th><th>التاريخ</th><th>الوصف الأساسي</th><th>المبلغ الكلي</th><th>المبلغ الذي سيخصم من أحمد</th></tr>"
        total_full = 0
        total_ahmed = 0
        
        for i, exp in enumerate(unassigned, 1):
            amt = float(exp.amount or 0)
            total_full += amt
            desc = exp.description or (exp.category.name if exp.category else '---')
            d = exp.date.strftime('%Y-%m-%d') if exp.date else '---'
            
            # استعراض החصة
            ratio = 1.0
            ratio_badge = "<span style='color:#ef9a9a'>(100% على أحمد)</span>"
            if 'شركاء' in desc:
                ratio = 0.0
                ratio_badge = "<span style='color:#8e99a4'>(لا يخص أحمد - شركاء فقط)</span>"
            elif 'مشترك' in desc or '50' in desc:
                ratio = 0.5
                ratio_badge = "<span style='color:#80d8ff'>(مشترك 50%)</span>"
                
            ahmed_amt = amt * ratio
            total_ahmed += ahmed_amt
            
            html += f"<tr><td>{i}</td><td>{d}</td><td>{desc}</td><td>{amt:,.2f}</td><td><b>{ahmed_amt:,.2f}</b> {ratio_badge}</td></tr>"
            
        html += f"<tr style='background:#333;font-weight:bold'><td colspan='3'>الإجمالي</td><td>{total_full:,.2f}</td><td style='color:#ffd700'>{total_ahmed:,.2f} ج.م بحد أقصى على أحمد</td></tr>"
        html += "</table>"
        html += f"<br><a href='/migrate-unassigned-expenses?confirm=yes' style='background:#ffd700;color:#000;padding:15px 30px;text-decoration:none;border-radius:10px;font-weight:bold;font-size:1.2em;display:inline-block'>⚡ تأكيد التوزيع والتعيين الفوري</a>"
    else:
        html += "<h3 style='color:#69f0ae'>✅ لا توجد مصاريف غير محددة — كل شيء مرتبط!</h3>"

    html += "</body></html>"
    return html


@app.route('/owner-settlement')
@login_required
def owner_settlement():
    if current_user.role != 'general_manager':
        return "غير مصرح", 403

    now = cairo_now()
    date_from = request.args.get('date_from', now.replace(day=1).strftime('%Y-%m-%d'))
    date_to = request.args.get('date_to', now.strftime('%Y-%m-%d'))

    try:
        d_from = datetime.strptime(date_from, '%Y-%m-%d')
        d_to = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
    except:
        d_from = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        d_to = now + timedelta(days=1)
        
    min_allowed_date = datetime(2026, 5, 1)
    if d_from < min_allowed_date:
        d_from = min_allowed_date
        date_from = d_from.strftime('%Y-%m-%d')

    # 1. جلب كل فواتير البيع (غير الـ proforma) في الفترة المحددة
    orders = SaleOrder.query.filter(
        SaleOrder.is_proforma == False,
        SaleOrder.date >= d_from,
        SaleOrder.date < d_to
    ).all()

    total_revenue = 0.0
    total_cost = 0.0
    total_shipping = 0.0
    
    # مقاييس جديدة
    total_gross_sales = 0.0
    total_returns_value = 0.0
    total_company_discounts = 0.0
    
    sales_details = []
    
    # عمولات الفريق والشركاء (محسوبة من الفواتير مباشرة)
    total_team_comm = 0.0
    team_comm_details = []
    total_partner_invoice_comm = 0.0
    total_partner_invoice_discount = 0.0
    total_partner_invoice_penalty = 0.0
    total_partner_net_comm = 0.0
    partner_comm_invoice_details = []

    for order in orders:
        order_total_qty = sum(item.quantity for item in order.items)
        returns_qty = sum(ret.total_qty or 0 for ret in order.return_invoices)
        net_qty = max(0, order_total_qty - returns_qty)
        
        returned_value = 0.0
        returned_cost = 0.0
        if order.return_invoices and order_total_qty > 0:
            movements = StockMovement.query.filter(StockMovement.reason.like(f"%مرتجع فاتورة #{order.id}%")).all()
            items_by_variant = {i.variant_id: i for i in order.items}
            for mv in movements:
                sale_item = items_by_variant.get(mv.variant_id)
                if sale_item:
                    returned_value += mv.quantity_change * sale_item.unit_price
                if mv.variant:
                    returned_cost += mv.quantity_change * (mv.variant.cost_price or 0)
                    
        net_total = round((order.total_amount or 0) - returned_value, 2)
        real_cost = sum(item.quantity * (item.variant.cost_price or 0) for item in order.items if item.variant)
        net_cost = round(real_cost - returned_cost, 2)

        is_under_partner = False
        partner_obj = None
        seller = order.sales_rep
        if seller:
            if seller.role == 'manager':
                is_under_partner = True
                partner_obj = seller
            elif seller.manager_id:
                mgr = db.session.get(User, seller.manager_id)
                if mgr and mgr.role == 'manager':
                    is_under_partner = True
                    partner_obj = mgr
                    
        actual_discount = (order.discount or 0) if net_qty > 0 else 0
        
        # الإيراد = قيمة المبيعات الصافية التي تدخل للشركة
        if is_under_partner:
            order_revenue = net_total # الخصم بيتحمله الشريك
            comp_discount = 0
        else:
            order_revenue = net_total - actual_discount # الخصم بتتحمله الشركة
            comp_discount = actual_discount
            
        order_cost = net_cost
        order_shipping = order.shipping_fee or 0

        total_revenue += order_revenue
        total_cost += order_cost
        total_shipping += order_shipping
        
        # تجميع المقاييس الجديدة
        total_gross_sales += (order.total_amount or 0)
        total_returns_value += returned_value
        total_company_discounts += comp_discount

        creator = order.sales_rep.fullname if order.sales_rep else 'غير معروف'
        returns_qty = sum(ret.total_qty or 0 for ret in order.return_invoices)
        
        # حساب العمولات بنفس منطق invoices.py
        order_est_comm = 0
        order_net_comm = 0
        if seller:
            if is_under_partner and partner_obj:
                order_est_comm = net_qty * float(partner_obj.commission_value or 13.0)
                ret_total_deduction = sum(r.total_deduction or 0 for r in order.return_invoices)
                order_net_comm = order_est_comm - actual_discount - ret_total_deduction
            else:
                # عمولة الفريق (الموظفين العاديين)
                from backend.helpers import calculate_user_commission
                # نحتاج نحسب إجمالي مبيعات الموظف في الشهر
                from datetime import datetime as dt2
                start_m = dt2(order.date.year, order.date.month, 1)
                if order.date.month == 12:
                    end_m = dt2(order.date.year + 1, 1, 1)
                else:
                    end_m = dt2(order.date.year, order.date.month + 1, 1)
                gross = db.session.query(func.sum(SaleItem.quantity)).join(SaleOrder).filter(
                    SaleOrder.user_id == seller.id, SaleOrder.is_proforma == False,
                    SaleOrder.date >= start_m, SaleOrder.date < end_m
                ).scalar() or 0
                
                returns_all = db.session.query(func.sum(ReturnInvoice.total_qty)).join(SaleOrder).filter(
                    SaleOrder.user_id == seller.id,
                    ReturnInvoice.date >= start_m, ReturnInvoice.date < end_m
                ).scalar() or 0
                
                net_for_tier = max(0, gross - returns_all)
                
                order_est_comm = calculate_user_commission(seller, net_qty, net_for_tier)
                order_net_comm = order_est_comm
        
        # تصنيف العمولة
        if is_under_partner:
            total_partner_invoice_comm += order_est_comm
            total_partner_invoice_discount += actual_discount
            ret_total_deduction = sum(r.total_deduction or 0 for r in order.return_invoices)
            total_partner_invoice_penalty += ret_total_deduction
            total_partner_net_comm += order_net_comm
            partner_comm_invoice_details.append({
                'date': order.date.strftime('%Y-%m-%d'),
                'invoice_id': order.id,
                'seller': creator,
                'comm': round(order_est_comm, 2),
                'discount': round(actual_discount, 2),
                'penalty': round(ret_total_deduction, 2),
                'net': round(order_net_comm, 2)
            })
        else:
            total_team_comm += order_net_comm
            team_comm_details.append({
                'date': order.date.strftime('%Y-%m-%d'),
                'invoice_id': order.id,
                'seller': creator,
                'comm': round(order_net_comm, 2)
            })

        sales_details.append({
            'date': order.date.strftime('%Y-%m-%d'),
            'invoice_id': order.id,
            'seller': creator,
            'revenue': round(order_revenue, 2),
            'cost': round(order_cost, 2),
            'shipping': round(order_shipping, 2),
            'has_return': returns_qty > 0,
            'returned_qty': returns_qty
        })

    total_all_commissions = total_team_comm + total_partner_net_comm
    initial_profit = total_revenue - total_cost - total_shipping - total_all_commissions

    # ======================================================
    # 2. المكافآت والجزاءات (admin_bonus / admin_penalty)
    # ======================================================
    gm_user = User.query.filter_by(role='general_manager').first()
    
    bonus_penalty_trans = PartnerTransaction.query.filter(
        PartnerTransaction.type.in_(['admin_bonus', 'admin_penalty']),
        PartnerTransaction.date >= d_from,
        PartnerTransaction.date < d_to
    ).all()

    total_bonuses = 0.0
    total_penalties_admin = 0.0
    bonus_penalty_details = []
    
    for t in bonus_penalty_trans:
        partner_name = t.partner.fullname if t.partner else '---'
        amt = abs(float(t.amount or 0))
        
        if t.type == 'admin_bonus':
            total_bonuses += amt
            desc = t.description or 'مكافأة'
            display_amt = amt
            badge = 'bonus'
        else:
            total_penalties_admin += amt
            desc = t.description or 'جزاء'
            display_amt = -amt
            badge = 'penalty'
            
        bonus_penalty_details.append({
            'date': t.date.strftime('%Y-%m-%d') if t.date else '---',
            'partner': partner_name,
            'desc': desc,
            'amount': round(display_amt, 2),
            'badge': badge
        })
    
    total_bonus_penalty_net = total_bonuses - total_penalties_admin

    # ======================================================
    # 3. نصيب أحمد من المصاريف (من جدول PartnerTransaction الفعلي)
    # ======================================================
    ahmed_expenses_total = 0.0
    ahmed_expenses_details = []

    if gm_user:
        ahmed_expense_trans = PartnerTransaction.query.filter(
            PartnerTransaction.partner_id == gm_user.id,
            PartnerTransaction.type == 'expense_share',
            PartnerTransaction.date >= d_from,
            PartnerTransaction.date < d_to
        ).all()

        for t in ahmed_expense_trans:
            amt = abs(float(t.amount or 0))
            ahmed_expenses_total += amt
            ahmed_expenses_details.append({
                'date': t.date.strftime('%Y-%m-%d') if t.date else '---',
                'desc': t.description or '---',
                'amount': round(amt, 2)
            })

    # ======================================================
    # 4. نصيب أحمد من رواتب الموظفين والسلف (من سجل الحركات الفعلي)
    # ======================================================
    ahmed_shared_staff_total = 0.0
    ahmed_shared_staff_details = []
    ahmed_advances_total = 0.0
    ahmed_advances_details = []

    if gm_user:
        ahmed_staff_trans = PartnerTransaction.query.filter(
            PartnerTransaction.partner_id == gm_user.id,
            PartnerTransaction.type == 'staff_expense',
            PartnerTransaction.date >= d_from,
            PartnerTransaction.date < d_to
        ).all()

        for t in ahmed_staff_trans:
            amt = abs(float(t.amount or 0))
            desc = t.description or ""
            
            if "سلفة" in desc:
                ahmed_advances_total += amt
                ahmed_advances_details.append({
                    'date': t.date.strftime('%Y-%m-%d') if t.date else '---',
                    'desc': desc,
                    'amount': round(amt, 2)
                })
            else:
                ahmed_shared_staff_total += amt
                share_type = "تحمل 100%"
                op_type = "مصروف طاقم"
                
                if "50%" in desc:
                    share_type = "مشترك (50/50)"
                
                if "راتب" in desc or "مرتب" in desc:
                    op_type = "راتب شهرى"

                ahmed_shared_staff_details.append({
                    'date': t.date.strftime('%Y-%m-%d') if t.date else '---',
                    'desc': desc,
                    'share_type': share_type,
                    'op_type': op_type,
                    'amount': round(amt, 2)
                })

    # ======================================================
    # 5. مسحوبات أحمد الشخصية
    # ======================================================
    ahmed_withdrawals_total = 0.0
    ahmed_withdrawals_details = []

    if gm_user:
        gm_trans = PartnerTransaction.query.filter(
            PartnerTransaction.partner_id == gm_user.id,
            PartnerTransaction.type == 'withdrawal',
            PartnerTransaction.date >= d_from,
            PartnerTransaction.date < d_to
        ).all()

        for t in gm_trans:
            amt = abs(float(t.amount or 0))
            ahmed_withdrawals_total += amt
            ahmed_withdrawals_details.append({
                'date': t.date.strftime('%Y-%m-%d'),
                'desc': t.description or 'سحب شخصي',
                'amount': round(amt, 2)
            })

    # ======================================================
    # 6. المصاريف العامة للشركة (is_shared=True) — لا تُنشئ PartnerTransaction
    # ======================================================
    general_expenses_total = 0.0
    general_expenses_details = []

    general_expense_records = Expense.query.filter(
        Expense.is_shared == True,
        Expense.date >= d_from,
        Expense.date < d_to
    ).all()

    for exp in general_expense_records:
        cat_name = exp.category.name if exp.category else 'نثريات'
        
        # استبعاد فئة تصحيح الخزنة
        if 'تصحيح' in cat_name:
            continue
            
        amt = float(exp.amount or 0)
        general_expenses_total += amt
        general_expenses_details.append({
            'date': exp.date.strftime('%Y-%m-%d') if exp.date else '---',
            'desc': exp.description or '---',
            'amount': round(amt, 2),
            'category': cat_name
        })

    # ======================================================
    # 7. صافي ربح الشركة النهائي
    # ======================================================
    final_net_profit = (initial_profit
                        - general_expenses_total
                        - ahmed_shared_staff_total
                        - ahmed_advances_total
                        - ahmed_withdrawals_total
                        - total_bonus_penalty_net) # المكافآت - الجزاءات

    return render_template('owner_settlement.html',
                           total_revenue=round(total_revenue, 2),
                           total_cost=round(total_cost, 2),
                           total_shipping=round(total_shipping, 2),
                           initial_profit=round(initial_profit, 2),
                           total_gross_sales=round(total_gross_sales, 2),
                           total_returns_value=round(total_returns_value, 2),
                           total_company_discounts=round(total_company_discounts, 2),
                           sales_details=sales_details,
                           # عمولات الفريق (من الفواتير)
                           total_team_comm=round(total_team_comm, 2),
                           team_comm_details=team_comm_details,
                           # عمولات الشركاء (من الفواتير)
                           total_partner_net_comm=round(total_partner_net_comm, 2),
                           total_partner_invoice_comm=round(total_partner_invoice_comm, 2),
                           total_partner_invoice_discount=round(total_partner_invoice_discount, 2),
                           total_partner_invoice_penalty=round(total_partner_invoice_penalty, 2),
                           partner_comm_invoice_details=partner_comm_invoice_details,
                           total_all_commissions=round(total_all_commissions, 2),
                           # المكافآت والجزاءات
                           total_bonuses=round(total_bonuses, 2),
                           total_penalties_admin=round(total_penalties_admin, 2),
                           total_bonus_penalty_net=round(total_bonus_penalty_net, 2),
                           bonus_penalty_details=bonus_penalty_details,
                           # المصاريف الإضافية
                           general_expenses_total=round(general_expenses_total, 2),
                           general_expenses_details=general_expenses_details,
                           ahmed_expenses_total=round(ahmed_expenses_total, 2),
                           ahmed_expenses_details=ahmed_expenses_details,
                           ahmed_shared_staff_total=round(ahmed_shared_staff_total, 2),
                           ahmed_shared_staff_details=ahmed_shared_staff_details,
                           ahmed_advances_total=round(ahmed_advances_total, 2),
                           ahmed_advances_details=ahmed_advances_details,
                           ahmed_withdrawals_total=round(ahmed_withdrawals_total, 2),
                           ahmed_withdrawals_details=ahmed_withdrawals_details,
                           # النهائي
                           final_net_profit=round(final_net_profit, 2),
                           date_from=date_from,
                           date_to=date_to)


@app.route('/partners/settle_all', methods=['POST'])
@general_manager_required
def partner_settlement_all():
    try:
        account_id = request.form.get('account_id')
        notes = request.form.get('notes', 'تصفية مجمعة (مقاصة أرباح وديون)')

        if not account_id:
            flash('يجب اختيار الخزينة', 'danger')
            return redirect(url_for('partners_report'))

        account = MoneyAccount.query.get(account_id)
        partners = User.query.filter_by(role='manager').all()

        net_payout = 0

        for p in partners:
            # حساب الرصيد الحالي (سواء موجب أو سالب)
            current_balance = db.session.query(func.sum(PartnerTransaction.amount)).filter_by(partner_id=p.id).scalar() or 0

            if current_balance != 0:
                # تصفير الحساب: لو له 100 بنسجل -100، لو عليه 100 بنسجل +100
                db.session.add(PartnerTransaction(
                    partner_id=p.id,
                    type='withdrawal',
                    amount=-current_balance,
                    description=f"تصفية مجمعة لتصفير الرصيد: {notes}",
                    date=cairo_now()
                ))
                # جمع جبري للمبالغ (الموجب بيزود والاصلي السالب بينقص من الإجمالي)
                net_payout += current_balance

        # تحديث الخزينة بالصافي النهائي
        if net_payout != 0:
            account.balance -= net_payout

            db.session.add(FinancialTransaction(
                account_id=account.id,
                type='expense' if net_payout > 0 else 'income',
                category='تصفية شركاء',
                amount=-net_payout,
                description=f"صافي صرف تصفية مجمعة للشركاء (مقاصة)",
                created_by_id=current_user.id,
                date=cairo_now()
            ))

            db.session.commit()
            flash(f'✅ تمت المقاصة بنجاح! الصافي المنصرف من الخزنة: {net_payout} ج.م، وتم تصفير حسابات الجميع.', 'success')
        else:
            flash('⚠️ الأرصدة مصفره بالفعل.', 'info')

    except Exception as e:
        db.session.rollback()
        flash(f'❌ حدث خطأ: {str(e)}', 'danger')

    return redirect(url_for('partners_report'))
