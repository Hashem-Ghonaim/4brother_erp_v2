"""
Routes: reports
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


@app.route('/reports')
@login_required
def reports_hub():
    # 1. التحقق من صلاحية رؤية التقارير
    if not current_user.has_perm('view_reports'):
        flash('غير مصرح لك بدخول التقارير', 'danger')
        return redirect(url_for('dashboard'))

    # 2. جلب قائمة المستخدمين المسموح برؤية بياناتهم
    accessible_ids = get_accessible_users()

    report_type = request.args.get('type', 'sales')

    # === إعداد تواريخ الفلتر ===
    today = date.today()
    # الافتراضي: من أول الشهر الحالي إلى اليوم
    default_start = today.replace(day=1).strftime('%Y-%m-%d')
    default_end = today.strftime('%Y-%m-%d')

    start_date_str = request.args.get('start_date', default_start)
    end_date_str = request.args.get('end_date', default_end)

    data = {}
    chart = {'labels': [], 'values': [], 'type': 'bar'}

    # === تقرير المبيعات (Sales) ===
    # === تقرير المبيعات (Sales) ===
    if report_type == 'sales':
        # فلتر أساسي للفترة المحددة
        base_filters = [
            SaleOrder.is_proforma == False,
            SaleOrder.user_id.in_(accessible_ids),
            func.to_char(SaleOrder.date, 'YYYY-MM-DD') >= start_date_str,
            func.to_char(SaleOrder.date, 'YYYY-MM-DD') <= end_date_str
        ]

        # 1. إجمالي المبيعات
        total_sales = db.session.query(func.sum(SaleOrder.final_total)).filter(*base_filters).scalar() or 0

        # 2. عدد الفواتير
        orders_count = SaleOrder.query.filter(*base_filters).count()

        # 3. إجمالي الخصومات
        total_discounts = db.session.query(func.sum(SaleOrder.discount)).filter(*base_filters).scalar() or 0

        # حساب رسوم الشحن وقيمة البضاعة
        total_shipping = db.session.query(func.sum(SaleOrder.shipping_fee)).filter(*base_filters).scalar() or 0
        merchandise_sales = total_sales - total_shipping

        # 4. [الجديد] إجمالي عدد القطع المباعة
        total_items_sold = db.session.query(func.sum(SaleItem.quantity))\
            .join(SaleOrder)\
            .filter(*base_filters)\
            .scalar() or 0

        # المنتجات الأكثر مبيعاً
        top_products = db.session.query(ProductModel.name, func.sum(SaleItem.quantity).label('qty'), func.sum(SaleItem.total_price).label('rev'))\
            .select_from(ProductModel).join(ProductVariant).join(SaleItem).join(SaleOrder)\
            .filter(*base_filters)\
            .group_by(ProductModel.name).order_by(text('qty DESC')).limit(10).all()

        discount_orders = SaleOrder.query.filter(SaleOrder.discount > 0, *base_filters).order_by(SaleOrder.date.desc()).limit(20).all()

        data = {
            'total_sales': total_sales,
            'orders_count': orders_count,
            'total_discount': total_discounts,
            'total_items_sold': int(total_items_sold), # <--- تم الإرسال هنا
            'top_products': top_products,
            'discount_orders': discount_orders,
            'total_shipping': total_shipping,
            'merchandise_sales': merchandise_sales
        }

        if top_products:
            chart = {'labels': [p.name for p in top_products[:5]], 'values': [p.qty for p in top_products[:5]], 'type': 'bar'}
    elif report_type == 'inventory':
        # 1. القيمة المالية
        total_cost_value = db.session.query(func.sum(ProductVariant.stock * ProductVariant.cost_price)).filter(ProductVariant.stock > 0).scalar() or 0
        total_sell_value = db.session.query(func.sum(ProductVariant.stock * ProductVariant.sell_price)).filter(ProductVariant.stock > 0).scalar() or 0
        expected_profit = total_sell_value - total_cost_value

        # 2. الإحصائيات
        total_items_count = db.session.query(func.sum(ProductVariant.stock)).filter(ProductVariant.stock > 0).scalar() or 0

        # منتجات نفذت (0 أو سالب)
        out_of_stock_count = ProductVariant.query.filter(ProductVariant.stock <= 4).count()

        # 3. [تصحيح] قائمة التنبيهات (تشمل النواقص + اللي خلص)
        # بنرتب تصاعدي عشان اللي رصيده 0 أو سالب يظهر الأول
        low_stock_items = db.session.query(ProductModel.name, ProductVariant.stock)\
            .join(ProductVariant)\
            .filter(ProductVariant.stock <= 5)\
            .order_by(ProductVariant.stock.asc())\
            .limit(50).all() # نعرض أهم 50 صنف فقط عشان الصفحة متبقاش طويلة

        # 4. الأكثر توفراً
        top_stock = db.session.query(ProductModel.name, ProductVariant.stock)\
            .join(ProductVariant).filter(ProductVariant.stock > 0)\
            .order_by(ProductVariant.stock.desc()).limit(10).all()

        # 5. منتجات أقل من 50 قطعة (تحتاج إعادة تخزين)
        needs_restock = db.session.query(ProductModel.name, ProductVariant.stock, ProductVariant.id)\
            .join(ProductVariant)\
            .filter(ProductVariant.stock < 50, ProductVariant.stock > 0)\
            .order_by(ProductVariant.stock.asc()).all()

        data = {
            'total_cost_value': total_cost_value,
            'total_sell_value': total_sell_value,
            'expected_profit': expected_profit,
            'total_items': total_items_count,
            'low_stock_items': low_stock_items,
            'out_of_stock_count': out_of_stock_count,
            'top_stock': top_stock,
            'needs_restock': needs_restock
        }

        if top_stock:
            chart = {
                'labels': [p[0] for p in top_stock[:10]],
                'values': [p[1] for p in top_stock[:10]],
                'type': 'bar'
            }
    # === التقرير المالي (Finance) ===
    # === تقرير الحضور والانصراف (Attendance) - الإضافة الجديدة ===
    elif report_type == 'attendance':
        # جلب السجلات في الفترة المحددة
        query = Attendance.query.filter(
            func.to_char(Attendance.date, 'YYYY-MM-DD') >= start_date_str,
            func.to_char(Attendance.date, 'YYYY-MM-DD') <= end_date_str
        )

        # ترتيب النتائج
        records = query.order_by(Attendance.date.desc(), Attendance.check_in.asc()).all()

        # إحصائيات سريعة
        total_present = len(records)
        total_late = sum(1 for r in records if r.status == 'late')

        # تجميع ساعات العمل
        total_seconds = 0
        attendance_list = []

        for r in records:
            work_hours_str = "---"
            if r.check_in and r.check_out:
                diff = r.check_out - r.check_in
                seconds = diff.total_seconds()
                total_seconds += seconds

                h = int(seconds // 3600)
                m = int((seconds % 3600) // 60)
                work_hours_str = f"{h}س {m}د"

            attendance_list.append({
                'id': r.id,
                'user': r.user.fullname,
                'role': r.user.role,
                'date': r.date,
                'check_in': r.check_in,
                'check_out': r.check_out,
                'status': r.status,
                'is_excused': getattr(r, 'is_excused', False),
                'work_hours': work_hours_str,
                'user_shift_start': r.user.shift_start,
                'user_shift_end': r.user.shift_end
            })

        total_hours_sum = int(total_seconds // 3600)

        data = {
            'records': attendance_list,
            'stats': {
                'total_present': total_present,
                'total_late': total_late,
                'total_hours': total_hours_sum
            }
        }
    elif report_type == 'finance':
        # 1. حساب مجمل الربح (Gross Profit) للفترة المحددة
        sales_condition = [
            SaleOrder.is_proforma == False,
            or_(SaleOrder.is_shipping == False, SaleOrder.shipping_status == 'settled'),
            SaleOrder.user_id.in_(accessible_ids),
            func.to_char(SaleOrder.date, 'YYYY-MM-DD') >= start_date_str,
            func.to_char(SaleOrder.date, 'YYYY-MM-DD') <= end_date_str
        ]

        total_rev = db.session.query(func.sum(SaleOrder.final_total - SaleOrder.shipping_fee)).filter(*sales_condition).scalar() or 0
        total_cogs = db.session.query(func.sum(SaleItem.quantity * ProductVariant.cost_price)).join(SaleOrder).filter(*sales_condition).join(ProductVariant).scalar() or 0
        gross = total_rev - total_cogs

        # 2. تحليل المصروفات وتجهيز بيانات الرسم البياني
        all_expenses = Expense.query.filter(
            func.to_char(Expense.date, 'YYYY-MM-DD') >= start_date_str,
            func.to_char(Expense.date, 'YYYY-MM-DD') <= end_date_str
        ).all()

        analysis = {
            'general':    {'total': 0, 'details': {}, 'color': 'success', 'icon': 'building', 'label': 'مصروفات عامة (شركة)'},
            'split':      {'total': 0, 'details': {}, 'color': 'dark', 'icon': 'scale-balanced', 'label': 'مشترك (50/50)'},
            'private_gm': {'total': 0, 'details': {}, 'color': 'primary', 'icon': 'user-tie', 'label': 'خاص (أحمد عبد الفتاح)'},
            'withdrawal': {'total': 0, 'details': {}, 'color': 'danger', 'icon': 'hand-holding-usd', 'label': 'مسحوبات شركاء'}
        }

        for exp in all_expenses:
            # ابحث عن حلقة (for exp in all_expenses) في قسم المالية واستبدل منطق الـ if بهذا:
            desc = exp.description if exp.description else ""

            # الترتيب الجديد للتعرف على النوع
            if any(word in desc for word in ["مشترك", "مقسم", "50%"]):
                exp_type = 'split'
            elif any(word in desc for word in ["خاص", "GM", "أحمد عبد الفتاح"]):
                exp_type = 'private_gm'
            elif any(word in desc for word in ["سحب", "مسحوبات"]):
                exp_type = 'withdrawal'
            elif exp.is_shared: # لو معلم عليه "عام" صراحة
                exp_type = 'general'
            else:
                # افتراضي لو مفيش أي علامة، نعتبره عام إلا لو وصفه فيه مشترك
                exp_type = 'general'

            analysis[exp_type]['total'] += exp.amount
            cat_name = exp.category.name if exp.category else "نثريات"
            if cat_name not in analysis[exp_type]['details']: analysis[exp_type]['details'][cat_name] = 0
            analysis[exp_type]['details'][cat_name] += exp.amount

        expenses_by_type = []
        for key, val in analysis.items():
            expenses_by_type.append({
                'id': key, 'name': val['label'], 'amount': val['total'],
                'color': val['color'], 'icon': val['icon'], 'details': val['details']
            })

        # 3. تجميع بيانات الرسم البياني (حسب بنود المصروفات)
        expenses_q = db.session.query(ExpenseCategory.name, func.sum(Expense.amount))\
            .join(Expense)\
            .filter(func.to_char(Expense.date, 'YYYY-MM-DD') >= start_date_str, func.to_char(Expense.date, 'YYYY-MM-DD') <= end_date_str)\
            .group_by(ExpenseCategory.name).all()

        # تجهيز متغير chart للمتصفح
        if expenses_q:
            chart = {
                'labels': [x[0] for x in expenses_q],
                'values': [float(x[1]) for x in expenses_q],
                'type': 'pie'
            }

        # 4. الحسابات النهائية للصافي
        total_exp = sum([e.amount for e in all_expenses])
        net = gross - total_exp # الربح - كل المصاريف (كما طلبت)

        finance_total_sales = db.session.query(func.sum(SaleOrder.final_total)).filter(*sales_condition).scalar() or 0
        finance_total_shipping = db.session.query(func.sum(SaleOrder.shipping_fee)).filter(*sales_condition).scalar() or 0

        total_refunds = db.session.query(func.sum(FinancialTransaction.amount)).filter(
            FinancialTransaction.type == 'refund',
            func.to_char(FinancialTransaction.date, 'YYYY-MM-DD') >= start_date_str,
            func.to_char(FinancialTransaction.date, 'YYYY-MM-DD') <= end_date_str
        ).scalar() or 0
        total_refunds = abs(total_refunds)

        data = {
            'gross_profit': round(gross, 2),
            'total_expenses': round(total_exp, 2),
            'net_profit': round(net, 2),
            'expenses_by_type': expenses_by_type,
            'total_rev': round(total_rev, 2),
            'total_cogs': round(total_cogs, 2),
            'finance_total_sales': round(finance_total_sales, 2),
            'finance_total_shipping': round(finance_total_shipping, 2),
            'total_refunds': round(total_refunds, 2)
        }


    # === تقرير الموردين (Suppliers) ===
    elif report_type == 'suppliers':
        # الديون تراكمية (لا تتأثر بالتاريخ)
        suppliers_debt = db.session.query(Supplier).filter(Supplier.balance != 0).order_by(Supplier.balance.desc()).all()
        total_debt = sum([s.balance for s in suppliers_debt if s.balance > 0])

        # المشتريات (تتأثر بالتاريخ)
        top_suppliers = db.session.query(Supplier.name, func.count(PurchaseOrder.id).label('orders_count'), func.sum(PurchaseOrder.total_cost).label('total_purchases'))\
            .join(PurchaseOrder)\
            .filter(func.to_char(PurchaseOrder.date, 'YYYY-MM-DD') >= start_date_str, func.to_char(PurchaseOrder.date, 'YYYY-MM-DD') <= end_date_str)\
            .group_by(Supplier.id).order_by(text('total_purchases DESC')).limit(5).all()

        data = {'suppliers_debt': suppliers_debt, 'total_debt': total_debt, 'top_suppliers': top_suppliers}
        if top_suppliers: chart = {'labels': [s.name for s in top_suppliers], 'values': [s.total_purchases for s in top_suppliers], 'type': 'doughnut'}

    # === تقرير الموارد البشرية (HR) ===
    elif report_type == 'hr':
        sales_reps = User.query.filter(User.id.in_(accessible_ids)).all()
        hr_report = []
        for emp in sales_reps:
            # فلترة المبيعات والطلبات والقطع بالتاريخ
            date_filter = [
                SaleOrder.user_id == emp.id,
                SaleOrder.is_proforma == False,
                func.to_char(SaleOrder.date, 'YYYY-MM-DD') >= start_date_str,
                func.to_char(SaleOrder.date, 'YYYY-MM-DD') <= end_date_str
            ]

            total_sales = db.session.query(func.sum(SaleOrder.final_total)).filter(*date_filter).scalar() or 0
            orders_count = SaleOrder.query.filter(*date_filter).count()

            total_items = db.session.query(func.sum(SaleItem.quantity))\
                .join(SaleOrder)\
                .filter(*date_filter).scalar() or 0

            # كل المرتجعات في هذه الفترة (للشريحة)
            returns_all = db.session.query(func.sum(ReturnInvoice.total_qty))\
                .join(SaleOrder)\
                .filter(SaleOrder.user_id == emp.id,
                        func.to_char(ReturnInvoice.date, 'YYYY-MM-DD') >= start_date_str,
                        func.to_char(ReturnInvoice.date, 'YYYY-MM-DD') <= end_date_str).scalar() or 0

            # المرتجعات اللي حصلت في نفس الفترة لفواتير من نفس الفترة (للعمولة)
            returns_same = db.session.query(func.sum(ReturnInvoice.total_qty))\
                .join(SaleOrder)\
                .filter(SaleOrder.user_id == emp.id,
                        func.to_char(ReturnInvoice.date, 'YYYY-MM-DD') >= start_date_str,
                        func.to_char(ReturnInvoice.date, 'YYYY-MM-DD') <= end_date_str,
                        func.to_char(SaleOrder.date, 'YYYY-MM-DD') >= start_date_str,
                        func.to_char(SaleOrder.date, 'YYYY-MM-DD') <= end_date_str).scalar() or 0

            net_for_tier = max(0, total_items - returns_all)
            net_for_pay = max(0, total_items - returns_same)

            commission = calculate_user_commission(emp, net_for_pay, net_for_tier)

            hr_report.append({
                'name': emp.fullname, 'role': emp.role, 'sales': total_sales,
                'orders': orders_count, 'items': total_items, 'commission': commission
            })
        data = {'hr_report': hr_report}

    # === تقرير العملاء (CRM) ===
    elif report_type == 'crm':
        # العملاء الأكثر شراءً (في الفترة المحددة)
        top_customers = db.session.query(Customer.name, func.count(SaleOrder.id).label('visits'), func.sum(SaleOrder.final_total).label('spent'))\
            .join(SaleOrder)\
            .filter(SaleOrder.is_proforma==False, SaleOrder.user_id.in_(accessible_ids))\
            .filter(func.to_char(SaleOrder.date, 'YYYY-MM-DD') >= start_date_str, func.to_char(SaleOrder.date, 'YYYY-MM-DD') <= end_date_str)\
            .group_by(Customer.id).order_by(text('spent DESC')).limit(10).all()

        # العملاء الجدد (في الفترة المحددة)
        new_customers = Customer.query.filter(
            func.to_char(Customer.created_at, 'YYYY-MM-DD') >= start_date_str,
            func.to_char(Customer.created_at, 'YYYY-MM-DD') <= end_date_str,
            Customer.created_by_id.in_(accessible_ids)
        ).count()

        # تحليل السلة (مع فلترة التاريخ)
        sql = text("""
            SELECT p1.name as p1_name, p2.name as p2_name, COUNT(*) as frequency 
            FROM sale_item i1 
            JOIN sale_item i2 ON i1.order_id = i2.order_id 
            JOIN sale_order o ON i1.order_id = o.id 
            JOIN product_variant v1 ON i1.variant_id = v1.id 
            JOIN product_variant v2 ON i2.variant_id = v2.id 
            JOIN product_model p1 ON v1.model_id = p1.id 
            JOIN product_model p2 ON v2.model_id = p2.id 
            WHERE i1.variant_id < i2.variant_id 
              AND o.is_proforma = 0 
              AND date(o.date) >= :start_date 
              AND date(o.date) <= :end_date
            GROUP BY p1.name, p2.name 
            ORDER BY frequency DESC LIMIT 5
        """)
        market_basket = db.session.execute(sql, {'start_date': start_date_str, 'end_date': end_date_str}).fetchall()

        data = {'top_customers': top_customers, 'market_basket': market_basket, 'new_customers': new_customers}
        if top_customers: chart = {'labels': [c.name for c in top_customers[:5]], 'values': [c.spent for c in top_customers[:5]], 'type': 'pie'}

    # تمرير التواريخ للقالب لعرضها في الفلتر
    return render_template('reports_hub.html', type=report_type, data=data, chart=chart, start_date=start_date_str, end_date=end_date_str)

@app.route('/audit/system_gap')
@general_manager_required
def audit_system_gap():
    try:
        report = []
        total_gap_valuation = 0

        # 1. كشف فروقات تقييم المخزون (أخطر سبب)
        # الفكرة: هنقارن (أعلى سعر اشترينا بيه الصنف) مع (سعر التكلفة الحالي المسجل)
        # لو السعر الحالي أقل من سعر الشراء، ده بيعمل عجز في قيمة المخزن مقارنة بدين المورد

        products = ProductVariant.query.filter(ProductVariant.stock > 0).all()

        report.append("<h3>1. تحليل فروقات أسعار التكلفة (Valuation Gap)</h3>")
        report.append("<table border='1' style='width:100%; border-collapse:collapse; text-align:center;'>")
        report.append("<tr style='background:#f2f2f2;'><th>المنتج</th><th>المخزون الحالي</th><th>سعر التكلفة الحالي</th><th>متوسط سعر الشراء الفعلي</th><th>فرق السعر</th><th>قيمة العجز</th></tr>")

        for p in products:
            # نجيب كل مرات الشراء للمنتج ده
            purchase_items = PurchaseItem.query.filter_by(variant_id=p.id).all()

            if not purchase_items: continue

            # حساب متوسط سعر الشراء الفعلي لهذا المنتج
            total_qty_bought = sum(item.quantity for item in purchase_items)
            total_cost_bought = sum(item.total_cost for item in purchase_items)

            if total_qty_bought > 0:
                avg_purchase_price = total_cost_bought / total_qty_bought
            else:
                avg_purchase_price = 0

            # لو سعر التكلفة الحالي (المسجل في الكارت) أقل من اللي اشترينا بيه
            # ده معناه إن المخزن متقيم بأقل من قيمته الحقيقية (وده سبب العجز)
            if p.cost_price < avg_purchase_price:
                diff = avg_purchase_price - p.cost_price
                gap_value = diff * p.stock # العجز = الفرق × الكمية الموجودة

                # تجاهل الفروقات التافهة (أقل من قرش)
                if gap_value > 1:
                    total_gap_valuation += gap_value
                    report.append(f"""
                    <tr>
                        <td>{p.model.name}</td>
                        <td>{p.stock}</td>
                        <td style='color:red'>{round(p.cost_price, 2)}</td>
                        <td style='color:green'>{round(avg_purchase_price, 2)}</td>
                        <td>{round(diff, 2)}</td>
                        <td style='font-weight:bold;'>{round(gap_value, 2)}</td>
                    </tr>
                    """)

        report.append(f"<tr><td colspan='5'><b>إجمالي عجز تقييم المخزون</b></td><td style='background:yellow; font-weight:bold'>{round(total_gap_valuation, 2)}</td></tr>")
        report.append("</table>")

        # 2. كشف التلاعب اليدوي في أرصدة الموردين
        report.append("<br><h3>2. تحليل أرصدة الموردين (هل تم تعديل الرصيد يدوياً؟)</h3>")
        report.append("<table border='1' style='width:100%; border-collapse:collapse; text-align:center;'>")
        report.append("<tr style='background:#f2f2f2;'><th>المورد</th><th>الرصيد الحالي (في السيستم)</th><th>الرصيد المفترض (فواتير - سداد)</th><th>الفرق (تعديل يدوي)</th></tr>")

        suppliers = Supplier.query.all()
        total_manual_diff = 0

        for s in suppliers:
            # الرصيد المفترض = (مجموع فواتير الشراء) - (مجموع السدادات)
            total_purchases = sum(o.total_cost for o in s.orders)
            total_payments = sum(p.amount for p in s.payments)
            calculated_balance = total_purchases - total_payments

            diff = s.balance - calculated_balance

            if abs(diff) > 1: # لو الفرق أكبر من جنيه
                total_manual_diff += diff
                report.append(f"""
                <tr>
                    <td>{s.name}</td>
                    <td>{round(s.balance, 2)}</td>
                    <td>{round(calculated_balance, 2)}</td>
                    <td style='color:red; font-weight:bold'>{round(diff, 2)}</td>
                </tr>
                """)

        report.append(f"<tr><td colspan='3'><b>إجمالي الفروقات اليدوية</b></td><td style='background:yellow; font-weight:bold'>{round(total_manual_diff, 2)}</td></tr>")
        report.append("</table>")

        # الخلاصة
        total_found = total_gap_valuation + total_manual_diff
        report.append(f"""
        <div style='margin-top:30px; padding:20px; background:#e8f0fe; border:2px solid #0d6efd;'>
            <h2>💡 ملخص التحقيق:</h2>
            <ul>
                <li>قيمة العجز الناتج عن تغيير أسعار التكلفة: <b>{round(total_gap_valuation, 2)}</b></li>
                <li>قيمة العجز الناتج عن تعديل أرصدة الموردين يدوياً: <b>{round(total_manual_diff, 2)}</b></li>
                <li style='font-size:1.5em; color:green'>إجمالي المبلغ الذي تم العثور عليه: <b>{round(total_found, 2)}</b></li>
            </ul>
        </div>
        <br><br>
        <a href='/dashboard' style='padding:10px 20px; background:#333; color:white; text-decoration:none;'>عودة</a>
        """)

        return "".join(report)

    except Exception as e:
        return f"حدث خطأ: {e}"

@app.route('/audit/manual_adjustments')
@general_manager_required
def audit_manual_adjustments():
    try:
        report = []
        report.append("<h3>3. تحليل التسويات اليدوية للمخزون (Manual Stock Adjustments)</h3>")
        report.append("<p>هذا التقرير يجمع كل المرات التي تم فيها إنقاص المخزون يدوياً (ليس بيع ولا مرتجع ولا تحويل فواتير).</p>")
        report.append("<table border='1' style='width:100%; border-collapse:collapse; text-align:center;'>")
        report.append("<tr style='background:#f2f2f2;'><th>المنتج</th><th>الكمية المحذوفة</th><th>سعر التكلفة</th><th>قيمة العجز</th><th>السبب المسجل</th><th>التاريخ</th></tr>")

        movements = StockMovement.query.filter(StockMovement.quantity_change < 0).all()

        total_manual_loss = 0

        for mov in movements:
            # === التعديل هنا: إضافة كلمة 'تحويل' لقائمة الاستثناءات ===
            # تجاهل: بيع، فاتورة، شراء، تحويل (من مسودة لفاتورة)
            is_sales = ('بيع' in mov.reason) or \
                       ('فاتورة' in mov.reason) or \
                       ('شراء' in mov.reason) or \
                       ('تحويل' in mov.reason)

            if not is_sales:
                variant = ProductVariant.query.get(mov.variant_id)
                if variant:
                    qty_lost = abs(mov.quantity_change)
                    cost = variant.cost_price
                    loss_value = qty_lost * cost

                    total_manual_loss += loss_value

                    report.append(f"""
                    <tr>
                        <td>{variant.model.name}</td>
                        <td style='color:red; font-weight:bold'>{mov.quantity_change}</td>
                        <td>{cost}</td>
                        <td style='background:#ffeeba;'>{loss_value}</td>
                        <td>{mov.reason}</td>
                        <td>{mov.timestamp.strftime('%Y-%m-%d')}</td>
                    </tr>
                    """)

        report.append(f"<tr><td colspan='3'><b>إجمالي قيمة البضاعة المحذوفة يدوياً</b></td><td style='background:red; color:white; font-weight:bold; font-size:1.2em;'>{round(total_manual_loss, 2)}</td><td colspan='2'></td></tr>")
        report.append("</table>")

        report.append(f"""
        <div style='margin-top:20px; padding:15px; border:2px solid #333;'>
            <h4>💡 الخلاصة:</h4>
            <p>المبلغ <b>{round(total_manual_loss, 2)}</b> هو الهالك الفعلي أو التعديل اليدوي الصريح (بعيداً عن المبيعات والتحويلات).</p>
        </div>
        <br>
        <a href='/dashboard' class='btn btn-dark'>عودة</a>
        """)

        return "".join(report)

    except Exception as e:
        return f"حدث خطأ: {e}"
