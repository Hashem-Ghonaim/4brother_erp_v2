from sqlalchemy import cast, Date
"""
Routes: dashboard
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


@app.route('/pos')
@login_required
def pos():
    # 1. التحقق: هل يوجد رقم فاتورة للتعديل في الرابط؟ (?edit=81)
    edit_id = request.args.get('edit')
    edit_order_data = None

    if edit_id:
        try:
            order = SaleOrder.query.get(int(edit_id))
            # نتأكد إنها موجودة وإنها "عرض سعر" (مسودة)
            if order and order.is_proforma:
                edit_order_data = {
                    'id': order.id,
                    'customer_id': order.customer_id,
                    'discount': order.discount or 0,
                    'paid_upfront': order.paid_upfront or 0,
                    'is_shipping': order.is_shipping,
                    'shipping_company_id': order.shipping_company_id,
                    'is_office_invoice': False,
                    'items': []
                }
                for item in order.items:
                    if item.variant:
                        edit_order_data['items'].append({
                            'id': item.variant.id,
                            'name': item.variant.model.name,
                            'price': item.unit_price,
                            'qty': item.quantity,
                            'stock': item.variant.stock,
                            'cost_price': item.variant.model.cost_price if item.variant.model else 0,
                            'barcode': item.variant.barcode or ''
                        })
        except Exception as e:
            print(f"Error fetching draft: {e}")

    # 2. البيانات العادية لصفحة البيع
    accessible_ids = get_accessible_users()
    customers = Customer.query.filter(
        or_(
            Customer.created_by_id.in_(accessible_ids),
            Customer.name == "عميل نقدي"
        )
    ).order_by(Customer.id.desc()).all()

    # 3. عرض الصفحة مع تمرير بيانات التعديل (لو وجدت)
    return render_template('pos.html',
                           categories=Category.query.all(),
                           products=ProductVariant.query.join(ProductModel).all(),
                           customers=customers,
                           shipping_companies=ShippingCompany.query.all(),
                           money_accounts=MoneyAccount.query.all(),
                           all_employees=User.query.all(),  # <--- إضافة الموظفين هنا
                           edit_order_data=edit_order_data) # <--- ده المهم عشان الجافاسكريبت يشتغل

@app.route('/hierarchy')
@login_required
def user_hierarchy():
    if current_user.fullname != "أحمد عبد الفتاح" and current_user.username != "gm_ahmed":
        flash("غير مصرح لك بالوصول إلى هذه الصفحة", "danger")
        return redirect(url_for('dashboard'))

    users = User.query.all()
    # Build a tree
    users_dict = {}
    for u in users:
        users_dict[u.id] = {
            'id': u.id,
            'fullname': u.fullname,
            'role': u.role,
            'username': u.username,
            'manager_id': u.manager_id,
            'children': []
        }
    
    root_users = []
    for uid, u in users_dict.items():
        if u['manager_id'] and u['manager_id'] in users_dict:
            users_dict[u['manager_id']]['children'].append(u)
        else:
            root_users.append(u)

    return render_template('hierarchy.html', root_users=root_users)


@app.route('/')
@app.route('/dashboard')
@login_required
def dashboard():
    today = cairo_now().date()
    month_str = today.strftime('%Y-%m')

    # تحديد الصلاحيات
    accessible_ids = get_accessible_users()

    # === 1. حساب مبيعات اليوم الصافية ===
    # أ) إجمالي الفواتير (Gross)
    today_gross = db.session.query(func.sum(SaleOrder.final_total))\
        .filter(cast(SaleOrder.date, Date) == today,
                SaleOrder.user_id.in_(accessible_ids),
                SaleOrder.is_proforma == False).scalar() or 0.0

    # ب) المرتجعات النقدية (Refunds) - قيمتها سالبة في الداتا بيز
    today_refunds = db.session.query(func.sum(FinancialTransaction.amount))\
        .filter(cast(FinancialTransaction.date, Date) == today,
                FinancialTransaction.type == 'refund',
                FinancialTransaction.created_by_id.in_(accessible_ids)).scalar() or 0.0

    # ج) الصافي = الفواتير + المرتجعات (بما أن المرتجعات سالبة، الجمع هنا يعني طرح)
    today_net_sales = today_gross + today_refunds

    # === 2. حساب مبيعات الشهر الصافية ===
    month_gross = db.session.query(func.sum(SaleOrder.final_total))\
        .filter(func.to_char(SaleOrder.date, 'YYYY-MM') == month_str,
                SaleOrder.user_id.in_(accessible_ids),
                SaleOrder.is_proforma == False).scalar() or 0.0

    month_refunds = db.session.query(func.sum(FinancialTransaction.amount))\
        .filter(func.to_char(FinancialTransaction.date, 'YYYY-MM') == month_str,
                FinancialTransaction.type == 'refund',
                FinancialTransaction.created_by_id.in_(accessible_ids)).scalar() or 0.0

    total_net_sales = month_gross + month_refunds

    # === 3. إحصائيات خاصة (الربح والعمولة) ===
    stats = {
        'net_profit': 0.0,
        'total_deductions': 0.0,
        'net_commission': 0.0,
        'net_items': 0
    }

    # تحديد تواريخ دقيقة لحساب المخزون
    now = cairo_now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if month_start.month == 12:
        month_end = month_start.replace(year=month_start.year + 1, month=1)
    else:
        month_end = month_start.replace(month=month_start.month + 1)

    # أ) لو المستخدم مدير
    if current_user.role in ['manager', 'general_manager']:
        # نستخدم جدول PartnerTransaction لحساب صافي الربح الدقيق
        trans = PartnerTransaction.query.filter(
            PartnerTransaction.partner_id == current_user.id,
            func.to_char(PartnerTransaction.date, 'YYYY-MM') == month_str
        ).all()

        # صافي الربح = مجموع كل الحركات (الدخل بالموجب والخصم بالسالب)
        stats['net_profit'] = sum(t.amount for t in trans)
        # إجمالي الخصومات للعرض
        stats['total_deductions'] = sum(abs(t.amount) for t in trans if t.amount < 0)

    # ب) لو المستخدم موظف
    # ... inside dashboard() function ...

    # b) If User is Employee (Sales / Worker)
    else:
        # 1. Gross Items Sold
        gross_items = db.session.query(func.sum(SaleItem.quantity))\
            .join(SaleOrder)\
            .filter(SaleOrder.user_id == current_user.id,
                    SaleOrder.is_proforma == False,
                    SaleOrder.date >= month_start,
                    SaleOrder.date < month_end).scalar() or 0

        # 2. Get Returned Items count from HR Transactions (The Accurate Way)
        # This matches the logic we added to the Profile page
        hr_trans = HRTransaction.query.filter(
            HRTransaction.user_id == current_user.id,
            HRTransaction.date >= month_start,
            HRTransaction.date < month_end
        ).all()

        returned_items_count = 0
        for t in hr_trans:
            # Look for the pattern "(5 pieces)" in the note
            if t.note and ('مرتجع' in t.note or 'قطعة' in t.note):
                match = re.search(r'\((\d+)\s*قطعة\)', t.note)
                if match:
                    returned_items_count += int(match.group(1))

        # 3. Net Items and Commission
        net_items_count = int(gross_items - returned_items_count)
        if net_items_count < 0: net_items_count = 0

        # جلب كل المرتجعات لتحديد الشريحة
        total_returns_for_tier = db.session.query(func.sum(ReturnInvoice.total_qty))\
            .join(SaleOrder)\
            .filter(SaleOrder.user_id == current_user.id,
                    ReturnInvoice.date >= month_start,
                    ReturnInvoice.date < month_end).scalar() or 0
        net_for_tier = int(gross_items - total_returns_for_tier)
        if net_for_tier < 0: net_for_tier = 0

        stats['net_items'] = net_items_count
        stats['net_commission'] = calculate_user_commission(current_user, net_items_count, net_for_tier)

    # 4. باقي البيانات
    team_members = []
    if current_user.role == 'general_manager':
        team_members = User.query.filter(User.id != current_user.id).all()
    elif current_user.role == 'manager':
        team_members = User.query.filter_by(manager_id=current_user.id).all()

    latest_orders = SaleOrder.query.filter(SaleOrder.user_id.in_(accessible_ids))\
        .order_by(SaleOrder.date.desc()).limit(5).all()

    attendance = Attendance.query.filter_by(user_id=current_user.id, date=today).first()

    all_managers = User.query.filter(User.role.in_(['manager', 'general_manager'])).all()

    # بناء شجرة الهيكل التنظيمي للمدير العام
    users_dict = {}
    for u in User.query.all():
        users_dict[u.id] = {
            'id': u.id, 'fullname': u.fullname, 'role': u.role, 
            'username': u.username, 'manager_id': u.manager_id, 'children': []
        }
    root_users = []
    for uid, u in users_dict.items():
        if u['manager_id'] and u['manager_id'] in users_dict:
            users_dict[u['manager_id']]['children'].append(u)
            
    root_users = []
    if current_user.role == 'general_manager' or current_user.username == 'gm_ahmed':
        for uid, u in users_dict.items():
            if not u['manager_id'] or u['manager_id'] not in users_dict:
                root_users.append(u)
    else:
        if current_user.id in users_dict:
            root_users.append(users_dict[current_user.id])

    return render_template('dashboard.html',
                         today_sales=round(today_net_sales, 2),
                         total_sales=round(total_net_sales, 2),
                         stats=stats,
                         team_members=team_members,
                         latest_orders=latest_orders,
                         attendance=attendance,
                         categories=Category.query.all(),
                         user=current_user,
                         all_managers=all_managers,
                         root_users=root_users)


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    # 1. تغيير كلمة المرور
    if request.method == 'POST':
        old_pass = request.form.get('old_password')
        new_pass = request.form.get('new_password')
        confirm_pass = request.form.get('confirm_password')
        if not check_password_hash(current_user.password, old_pass):
            flash('كلمة المرور الحالية غير صحيحة ❌', 'danger')
        elif new_pass != confirm_pass:
            flash('كلمة المرور الجديدة غير متطابقة ⚠️', 'warning')
        else:
            current_user.password = generate_password_hash(new_pass)
            db.session.commit()
            flash('تم تغيير كلمة المرور بنجاح ✅', 'success')
        return redirect(url_for('profile'))

    u = User.query.get(current_user.id)

    # === تحديد نطاق التاريخ للتقرير (من مدخلات المستخدم أو الشهر الحالي افتراضيًا) ===
    now = cairo_now()
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')

    if start_date_str and end_date_str:
        try:
            month_start = datetime.strptime(start_date_str, '%Y-%m-%d')
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
            # إضافة يوم للـ end_date علشان البحث بـ < month_end يشمل اليوم كلو
            month_end = end_date + timedelta(days=1)
        except ValueError:
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if month_start.month == 12:
                month_end = month_start.replace(year=month_start.year + 1, month=1)
            else:
                month_end = month_start.replace(month=month_start.month + 1)
            start_date_str = month_start.strftime('%Y-%m-%d')
            
            end_date_actual = month_end - timedelta(days=1)
            end_date_str = end_date_actual.strftime('%Y-%m-%d')
    else:
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # حساب أول يوم في الشهر القادم
        if month_start.month == 12:
            month_end = month_start.replace(year=month_start.year + 1, month=1)
        else:
            month_end = month_start.replace(month=month_start.month + 1)
        start_date_str = month_start.strftime('%Y-%m-%d')
        end_date_actual = month_end - timedelta(days=1)
        end_date_str = end_date_actual.strftime('%Y-%m-%d')

    mgr_data = {}
    emp_data = {}

    # === أ) المدير العام (General Manager) ===
    if u.role == 'general_manager':
        # 1. إجمالي المبيعات (صافي من المرتجعات) والتكلفة الكلية
        # نجلب كل فواتير البيع غير الـ proforma في هذا الشهر
        gm_orders = db.session.query(SaleOrder).filter(
            SaleOrder.is_proforma == False,
            SaleOrder.date >= month_start,
            SaleOrder.date < month_end
        ).all()

        total_sales_revenue = 0.0
        total_sales_cost = 0.0
        sales_details = []
        cost_details = []

        # لحساب مبيعات كل شخص
        user_sales_map = {}
        user_cost_map = {}

        for order in gm_orders:
            order_revenue = order.final_total
            order_cost = sum(item.quantity * (item.variant.cost_price or 0) for item in order.items if item.variant)
            
            # خصم المرتجعات لهذه الفاتورة
            if order.return_invoices:
                for ret in order.return_invoices:
                    order_revenue -= ret.total_deduction

            total_sales_revenue += order_revenue
            total_sales_cost += order_cost

            # تجميع لكل موظف/شريك
            creator = order.sales_rep.fullname if order.sales_rep else 'غير معروف'
            user_sales_map[creator] = user_sales_map.get(creator, 0) + order_revenue
            user_cost_map[creator] = user_cost_map.get(creator, 0) + order_cost

        for name, amount in user_sales_map.items():
            if amount > 0:
                sales_details.append({'date': month_start.strftime('%Y-%m'), 'invoice_label': name, 'desc': f'إجمالي مبيعات {name}', 'amount': amount})
        for name, amount in user_cost_map.items():
            if amount > 0:
                cost_details.append({'date': month_start.strftime('%Y-%m'), 'invoice_label': name, 'desc': f'إجمالي تكلفة مبيعات {name}', 'amount': amount})

        initial_net_profit = total_sales_revenue - total_sales_cost

        # 4. إجمالي عمولات كل الناس (كل ما هو مسجل كعمولة في حسابات الشركاء)
        all_comm_trans = PartnerTransaction.query.filter(
            PartnerTransaction.type.in_(['commission_gross', 'sub_commission']),
            PartnerTransaction.date >= month_start,
            PartnerTransaction.date < month_end
        ).all()
        
        total_commissions = 0.0
        comm_details = []
        for t in all_comm_trans:
            val = abs(float(t.amount)) if t.amount else 0.0
            total_commissions += val
            owner_name = t.partner.fullname if t.partner else '---'
            comm_details.append({
                'date': t.date.strftime('%Y-%m-%d'),
                'invoice_label': owner_name,
                'desc': t.description or t.type,
                'amount': val
            })

        # 5. مصاريف والتزامات الإدارة العليا (إجمالي مصاريف الشركة - مصاريف المديرين الأربعة)
        
        # أ. إجمالي مصاريف الشركة المسجلة في بند المصروفات
        company_expenses_raw = db.session.query(func.sum(Expense.amount)).filter(
            Expense.date >= month_start,
            Expense.date < month_end
        ).scalar() or 0.0
        company_expenses = abs(float(company_expenses_raw))

        # ب. إجمالي كافة المستحقات المخصومة من المديرين الأربعة (مصاريف، عمالة، عمولات، سحوبات، إلخ)
        manager_ids = [m.id for m in User.query.filter_by(role='manager').all()]
        managers_total_deductions = db.session.query(func.sum(PartnerTransaction.amount)).filter(
            PartnerTransaction.partner_id.in_(manager_ids),
            PartnerTransaction.type.in_(['expense_share', 'withdrawal', 'sub_commission', 'discount_deduction', 'staff_expense', 'return_penalty']),
            PartnerTransaction.date >= month_start,
            PartnerTransaction.date < month_end
        ).scalar() or 0.0
        
        # نجعل القيمة موجبة لأنها مسجلة بالسالب في الداتا بيز غالباً
        managers_total_deductions = abs(float(managers_total_deductions))

        # ج. الحساب النهائي لمصاريف الإدارة العليا
        # إجمالي المصاريف الكلية ناقصاً كل التزامات ومصروفات وسحوبات مديري الفروع
        gm_expenses = company_expenses - managers_total_deductions

        gm_expenses_details = [
            {'date': month_start.strftime('%Y-%m'), 'invoice_label': 'إجمالي تصنيفات', 'desc': 'إجمالي مصاريف الشركة الكلية', 'amount': company_expenses},
            {'date': month_start.strftime('%Y-%m'), 'invoice_label': 'مخصوم من الفروع', 'desc': 'إجمالي مستقطعات وسحوبات ومصاريف الشركاء', 'amount': -managers_total_deductions}
        ]

        # 6. تفريغ المصاريف والعمولات الخاصة بأحمد شخصياً ليراها في قسم "المصاريف الثابتة"
        gm_personal_trans = PartnerTransaction.query.filter(
            PartnerTransaction.partner_id == u.id,
            PartnerTransaction.type.in_(['staff_expense', 'expense_share', 'withdrawal', 'sub_commission', 'discount_deduction', 'return_penalty']),
            PartnerTransaction.date >= month_start,
            PartnerTransaction.date < month_end
        ).all()

        # توزيع حركات أحمد لمعرفة ما هو مدرج مسبقاً في الشركة وما هو جديد
        ahmed_commissions_val = sum(abs(float(t.amount)) for t in gm_personal_trans if t.type == 'sub_commission')
        ahmed_expenses_val = sum(abs(float(t.amount)) for t in gm_personal_trans if t.type in ['staff_expense', 'expense_share', 'withdrawal'])
        ahmed_penalties_val = sum(abs(float(t.amount)) for t in gm_personal_trans if t.type in ['discount_deduction', 'return_penalty'])

        ahmed_fixed_expenses = ahmed_commissions_val + ahmed_expenses_val + ahmed_penalties_val

        gm_personal_details = []
        for t in gm_personal_trans:
            val = abs(float(t.amount)) if t.amount else 0.0
            gm_personal_details.append({
                'date': t.date.strftime('%Y-%m-%d'),
                'invoice_label': 'رواتب' if t.type == 'staff_expense' else 'عمولة' if t.type == 'sub_commission' else t.type,
                'desc': t.description or t.type,
                'amount': val
            })

        # 7. فصل مصاريف وعمولات أحمد عن مصاريف وعمولات الشركة العامة لمنع الازدواجية
        corporate_commissions = max(0, total_commissions - ahmed_commissions_val)
        corporate_expenses = max(0, gm_expenses - ahmed_expenses_val)

        # 8. صافي الربح النهائي لأحمد عبد الفتاح
        gm_net_profit = initial_net_profit - corporate_commissions - corporate_expenses - ahmed_fixed_expenses

        mgr_data = {
            'is_gm': True,
            'total_sales': round(total_sales_revenue, 2),
            'total_cost': round(total_sales_cost, 2),
            'initial_profit': round(initial_net_profit, 2),
            'total_commissions': round(corporate_commissions, 2),
            'gm_expenses': round(corporate_expenses, 2),
            'gm_personal_deductions': round(ahmed_fixed_expenses, 2),
            'net_profit': round(gm_net_profit, 2),
            # Details arrays for modals
            'sales_details': sales_details,
            'cost_details': cost_details,
            'comm_details': comm_details,
            'gm_expenses_details': gm_expenses_details,
            'gm_personal_details': gm_personal_details
        }

    # === ب) المدير الموزع (Manager) ===
    elif u.role == 'manager':
        # 1. جلب كل حركات الشهر للمدير
        period_trans = PartnerTransaction.query.filter(
            PartnerTransaction.partner_id == u.id,
            PartnerTransaction.date >= month_start,
            PartnerTransaction.date < month_end
        ).all()

        def safe_float(val): return float(val) if val else 0.0

        # In the DB, 'commission_gross' is recorded as negative (expense to the company)
        gross_comm_val = abs(sum(safe_float(t.amount) for t in period_trans if t.type == 'commission_gross'))
        team_items_net = int(gross_comm_val / 13.0) if gross_comm_val > 0 else 0

        # Note: in DB they are negative. We make them positive for the profile display
        girls_comm = abs(sum(safe_float(t.amount) for t in period_trans if t.type == 'sub_commission'))
        discounts = abs(sum(safe_float(t.amount) for t in period_trans if t.type == 'discount_deduction'))
        returns = abs(sum(safe_float(t.amount) for t in period_trans if t.type == 'return_penalty'))
        # Exclude general expenses, staff costs, and withdrawals from "personal team profit"
        expenses = abs(sum(safe_float(t.amount) for t in period_trans if t.type == 'expense_share'))
        staff_costs = abs(sum(safe_float(t.amount) for t in period_trans if t.type == 'staff_expense'))
        withdrawals = abs(sum(safe_float(t.amount) for t in period_trans if t.type == 'withdrawal'))

        # Net profit formula for the manager includes ALL deductions (returns, discounts, expenses, staff costs, withdrawals)
        net_profit = gross_comm_val - (girls_comm + discounts + returns + expenses + staff_costs + withdrawals)

        def build_details(trans_list, trans_type):
            return [
                {
                    'amount': t.amount,
                    'desc': t.description or '---',
                    'date': t.date.strftime('%Y-%m-%d'),
                    'order_id': t.order_id,
                    'invoice_label': f"فاتورة #{t.order_id}" if t.order_id else "---"
                }
                for t in trans_list if t.type == trans_type
            ]

        mgr_data = {
            'is_gm': False,
            'team_items_count': team_items_net,
            'gross_comm': round(gross_comm_val, 2),
            'girls_comm': round(girls_comm, 2),
            'discounts': round(discounts, 2),
            'returns': round(returns, 2),
            'expenses': round(expenses, 2), # Keeping this just in case template needs it
            'staff_costs': round(staff_costs, 2),
            'withdrawals': round(withdrawals, 2),
            'net_profit': round(net_profit, 2),
            # Details for drill-down modal (includes ALL deductions and expenses)
            'gross_comm_details': build_details(period_trans, 'commission_gross'),
            'sales_comm_details': build_details(period_trans, 'sub_commission'),
            'deductions_details': build_details(period_trans, 'discount_deduction') + build_details(period_trans, 'return_penalty') + build_details(period_trans, 'expense_share') + build_details(period_trans, 'staff_expense') + build_details(period_trans, 'withdrawal')
        }


    # === ب) الموظف (Sales / Worker) - هنا كان اللغز ===
    else:
        # 1. إجمالي البيع من الفواتير (نطاق التاريخ الدقيق)
        # هام: نتأكد أن الفاتورة ليست proforma
        gross_items_sold = db.session.query(func.sum(SaleItem.quantity))\
            .join(SaleOrder)\
            .filter(SaleOrder.user_id == u.id,
                    SaleOrder.is_proforma == False,
                    SaleOrder.date >= month_start, # أكبر من أو يساوي 1 في الشهر
                    SaleOrder.date < month_end).scalar() or 0 # أصغر من 1 في الشهر الجاي

        # 2. قراءة المرتجعات من ملاحظات الـ HR (نطاق التاريخ الدقيق)
        hr_trans = HRTransaction.query.filter(
            HRTransaction.user_id == u.id,
            HRTransaction.date >= month_start,
            HRTransaction.date < month_end
        ).all()

        bonuses = 0
        real_deductions = 0
        advances = 0
        returned_items_from_hr = 0

        for t in hr_trans:
            if t.type == 'bonus':
                bonuses += t.amount
            elif t.type == 'advance':
                advances += t.amount
            elif t.type == 'deduction':
                # استخراج عدد القطع المرتجعة
                if t.note and ('مرتجع' in t.note or 'قطعة' in t.note):
                    match = re.search(r'\((\d+)\s*قطعة\)', t.note)
                    if match:
                        returned_items_from_hr += int(match.group(1))
                else:
                    real_deductions += t.amount
            elif t.type == 'penalty':
                 real_deductions += t.amount

        # 3. صافي القطع للعمولة (مبيعات - مرتجعات نفس الشهر فقط)
        net_items = gross_items_sold - returned_items_from_hr
        if net_items < 0: net_items = 0

        # جلب كل المرتجعات لتحديد الشريحة
        total_returns_for_tier = db.session.query(func.sum(ReturnInvoice.total_qty))\
            .join(SaleOrder)\
            .filter(SaleOrder.user_id == u.id,
                    ReturnInvoice.date >= month_start,
                    ReturnInvoice.date < month_end).scalar() or 0
        net_for_tier = int(gross_items_sold - total_returns_for_tier)
        if net_for_tier < 0: net_for_tier = 0

        # 4. حساب العمولة
        commission = calculate_user_commission(u, net_items, net_for_tier)

        # 5. حساب جزاءات الحضور
        month_str_att = month_start.strftime('%Y-%m')
        att_settings = AttendanceSettings.query.first()
        if not att_settings:
            att_settings = AttendanceSettings()
        daily_rate = (u.base_salary or 0) / 30
        attendance_deduction = 0
        attendance_details = []

        if not u.has_flexible_hours:
            attendance_records = Attendance.query.filter(
                Attendance.user_id == u.id,
                func.to_char(Attendance.date, 'YYYY-MM') == month_str_att
            ).all()
            for rec in attendance_records:
                if att_settings.skip_friday and rec.date.weekday() == 4: continue
                if att_settings.skip_saturday and rec.date.weekday() == 5: continue
                excuse = EmployeeExcuse.query.filter_by(user_id=u.id, date=rec.date).first()
                day_deduction = 0
                day_reason = ''
                if rec.status == 'absent':
                    if excuse and excuse.type == 'day':
                        day_deduction = daily_rate * att_settings.absent_full_day_excuse
                        day_reason = f'غياب بإذن يوم كامل (خصم {att_settings.absent_full_day_excuse} يوم)' if att_settings.absent_full_day_excuse > 0 else 'غياب بإذن يوم كامل (بدون خصم)'
                    elif getattr(rec, 'is_excused', False):
                        day_deduction = daily_rate * att_settings.absent_excused
                        day_reason = f'غياب بإذن (خصم {att_settings.absent_excused} يوم)'
                    else:
                        day_deduction = daily_rate * att_settings.absent_no_excuse
                        day_reason = f'غياب بدون إذن (خصم {att_settings.absent_no_excuse} يوم)'
                else:
                    late_mins = 0
                    if rec.check_in and u.shift_start:
                        shift_t = datetime.strptime(u.shift_start, '%H:%M').time()
                        check_in_t = rec.check_in.time()
                        shift_minutes = shift_t.hour * 60 + shift_t.minute
                        checkin_minutes = check_in_t.hour * 60 + check_in_t.minute
                        late_mins = max(0, checkin_minutes - shift_minutes)
                    early_mins = 0
                    if rec.check_out and u.shift_end:
                        shift_end_t = datetime.strptime(u.shift_end, '%H:%M').time()
                        check_out_t = rec.check_out.time()
                        end_minutes = shift_end_t.hour * 60 + shift_end_t.minute
                        checkout_minutes = check_out_t.hour * 60 + check_out_t.minute
                        if u.shift_start:
                            shift_start_t = datetime.strptime(u.shift_start, '%H:%M').time()
                            shift_minutes_chk = shift_start_t.hour * 60 + shift_start_t.minute
                            if end_minutes <= shift_minutes_chk:
                                end_minutes += 1440
                                if checkout_minutes < shift_minutes_chk:
                                    checkout_minutes += 1440
                        early_mins = max(0, end_minutes - checkout_minutes)
                    elif not rec.check_out:
                        if not u.has_flexible_hours:
                            day_deduction = daily_rate * att_settings.no_checkout_penalty
                            day_reason = f'لم يسجل انصراف (خصم {att_settings.no_checkout_penalty} يوم)'
                        attendance_deduction += day_deduction
                        if day_deduction > 0:
                            attendance_details.append({
                                'date': rec.date.strftime('%Y-%m-%d'),
                                'reason': day_reason,
                                'deduction': round(day_deduction, 2)
                            })
                        continue
                    total_lost_mins = late_mins + early_mins
                    if excuse and excuse.type == 'hours':
                        total_lost_mins = max(0, total_lost_mins - (excuse.hours * 60))
                    if total_lost_mins > att_settings.grace_period:
                        if total_lost_mins <= att_settings.tier1_max_mins:
                            day_deduction = daily_rate * att_settings.tier1_penalty
                        elif total_lost_mins <= att_settings.tier2_max_mins:
                            day_deduction = daily_rate * att_settings.tier2_penalty
                        elif total_lost_mins <= att_settings.tier3_max_mins:
                            day_deduction = daily_rate * att_settings.tier3_penalty
                        else:
                            day_deduction = daily_rate * att_settings.tier4_penalty
                    reasons = []
                    if late_mins > 0: reasons.append(f'تأخير {int(late_mins)} دقيقة')
                    if early_mins > 0: reasons.append(f'انصراف مبكر {int(early_mins)} دقيقة')
                    day_reason = ' + '.join(reasons) if reasons else 'حضر وانصرف في موعده'
                attendance_deduction += day_deduction
                if day_deduction > 0:
                    attendance_details.append({
                        'date': rec.date.strftime('%Y-%m-%d'),
                        'reason': day_reason,
                        'deduction': round(day_deduction, 2)
                    })

        # 6. خصم المرتجعات (محسوب من HR)
        returns_deduction = sum(t.amount for t in hr_trans if t.type == 'deduction' and t.note and ('مرتجع' in t.note or 'قطعة' in t.note))

        # 7. الراتب النهائي
        net_salary = (u.base_salary or 0) + commission + bonuses - real_deductions - advances - attendance_deduction

        emp_data = {
            'total_items': int(net_items),
            'commission': round(commission, 2),
            'bonuses': round(bonuses, 2),
            'deductions': round(real_deductions, 2),
            'advances': round(advances, 2),
            'attendance_deduction': round(attendance_deduction, 2),
            'attendance_details': attendance_details,
            'returns_deduction': round(returns_deduction, 2),
            'net_salary': round(net_salary, 2),
            'debug_gross': int(gross_items_sold),
            'debug_returns': int(returned_items_from_hr)
        }

    return render_template('profile.html', user=u, mgr=mgr_data, emp=emp_data,
                           start_date=start_date_str, end_date=end_date_str)

@app.route('/api/welcome_seen', methods=['POST'])
@login_required
def welcome_seen():
    current_user.has_seen_winter27_welcome = True
    db.session.commit()
    return jsonify({'status': 'success'})
