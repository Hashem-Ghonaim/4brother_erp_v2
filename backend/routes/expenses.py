from sqlalchemy import cast, Date
"""
Routes: expenses
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


@app.route('/expenses/details')
@general_manager_required
def expenses_details():
    # 1. استقبال فلاتر التاريخ (الافتراضي: الشهر الحالي)
    today = date.today()
    default_start = today.replace(day=1).strftime('%Y-%m-%d')
    default_end = today.strftime('%Y-%m-%d')

    start_date = request.args.get('start_date', default_start)
    end_date = request.args.get('end_date', default_end)
    category_filter = request.args.get('category_id', 'all')

    # 2. الاستعلام الأساسي (فلترة بالتاريخ)
    query = Expense.query.filter(
        cast(Expense.date, Date) >= start_date,
        cast(Expense.date, Date) <= end_date
    ).order_by(Expense.date.desc())

    # 3. تطبيق فلتر التصنيف
    if category_filter != 'all':
        query = query.filter(Expense.category_id == category_filter)

    expenses = query.all()

    # 4. حساب الإجماليات
    total_amount = sum(e.amount for e in expenses)

    # 5. البيانات المساعدة للقوائم
    categories = ExpenseCategory.query.all()

    return render_template('expenses_details.html',
                           expenses=expenses,
                           total_amount=total_amount,
                           categories=categories,
                           start_date=start_date,
                           end_date=end_date,
                           selected_cat=category_filter)
# 1. نعدل دالة العرض عشان نبعت الخزائن للصفحة

@app.route('/expenses', methods=['GET', 'POST'])
@general_manager_required
def expenses():
    # 1. إضافة بند مصروف جديد
    if 'add_category' in request.form:
        cat_name = request.form.get('new_category_name')
        if cat_name:
            if not ExpenseCategory.query.filter_by(name=cat_name).first():
                db.session.add(ExpenseCategory(name=cat_name))
                db.session.commit()
                flash('تم إضافة البند', 'success')
        return redirect(url_for('expenses'))

    # 2. إضافة مصروف جديد
    if 'add_expense' in request.form:
        try:
            amount = float(request.form.get('amount'))
            description = request.form.get('description')
            cat_id = request.form.get('category_id')
            expense_type = request.form.get('expense_type')
            account_id = request.form.get('account_id') # الخزينة

            account = MoneyAccount.query.get(account_id)
            if not account:
                flash('يجب اختيار خزينة', 'danger'); return redirect(url_for('expenses'))

            # إنشاء كائن المصروف (افتراضياً عام للشركة لمنع ظهور "غير محدد")
            new_expense = Expense(
                category_id=cat_id,
                amount=amount,
                description=description,
                user_id=current_user.id,
                account_id=account.id,
                is_shared=True  # جعل الوضع الافتراضي "مصروف عام"
            )

            # 3. تحديد النوع وتوزيع المصاريف (Expense Types & Distribution)
            # الأنواع المتاحة: general, withdrawal, private_gm, shared_50_50, partners_only
            if expense_type == 'general':
                new_expense.is_shared = True
                new_expense.description = f"{description}".strip()
                
            elif expense_type == 'withdrawal':
                new_expense.is_shared = False
                partner_id = request.form.get('partner_id')
                if not partner_id:
                    flash('يجب اختيار الشريك للمسحوبات', 'danger'); return redirect(url_for('expenses'))
                new_expense.description = f"سحب شخصي: {description}".strip()
                db.session.add(PartnerTransaction(
                    partner_id=partner_id, type='withdrawal', amount=-amount,
                    description=new_expense.description, date=cairo_now()
                ))
                
            elif expense_type == 'private_gm':
                new_expense.is_shared = False
                new_expense.description = f"مصروف خاص م/أحمد: {description}".strip()
                gm = User.query.filter_by(role='general_manager').first()
                if gm:
                    db.session.add(PartnerTransaction(
                        partner_id=gm.id, type='expense_share', amount=-amount, 
                        description=new_expense.description, date=cairo_now()
                    ))
                    
            elif expense_type == 'shared_50_50':
                new_expense.is_shared = False
                new_expense.description = f"مشترك (50/50): {description}".strip()
                
                gm = User.query.filter_by(role='general_manager').first()
                partners = User.query.filter_by(role='manager').all()
                
                if gm and partners:
                    # 50% for GM
                    gm_share = amount / 2
                    db.session.add(PartnerTransaction(
                        partner_id=gm.id, type='expense_share', amount=-gm_share,
                        description=f"{new_expense.description} (حصة 50%)", date=cairo_now()
                    ))
                    
                    # 50% divided among partners
                    if len(partners) > 0:
                        partner_share = gm_share / len(partners)
                        for p in partners:
                            db.session.add(PartnerTransaction(
                                partner_id=p.id, type='expense_share', amount=-partner_share,
                                description=f"{new_expense.description} (حصة شريك)", date=cairo_now()
                            ))
                            
            elif expense_type == 'partners_only':
                new_expense.is_shared = False
                new_expense.description = f"شركاء فقط (يوزع على 4): {description}".strip()
                
                partners = User.query.filter_by(role='manager').all()
                if partners and len(partners) > 0:
                    partner_share = amount / len(partners)
                    for p in partners:
                        db.session.add(PartnerTransaction(
                            partner_id=p.id, type='expense_share', amount=-partner_share,
                            description=f"{new_expense.description} (حصة شريك)", date=cairo_now()
                        ))

            # حفظ المصروف الأساسي في جدول المصروفات
            db.session.add(new_expense)

            # خصم الفلوس من الخزينة
            account.balance -= amount
            db.session.add(FinancialTransaction(
                account_id=account.id, type='expense', category='مصروفات',
                amount=-amount, description=f"صرف: {description}", created_by_id=current_user.id
            ))

            db.session.commit()
            flash('تم تسجيل المصروف ✅', 'success')

        except Exception as e:
            db.session.rollback()
            flash(f'خطأ: {e}', 'danger')

        return redirect(url_for('expenses'))

    # العرض
    categories = ExpenseCategory.query.all()
    partners = User.query.filter_by(role='manager').all()
    accounts = MoneyAccount.query.all()

    # فلاتر البحث
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    filter_cat = request.args.get('filter_category', '', type=str)
    filter_type = request.args.get('filter_type', '', type=str)
    filter_account = request.args.get('filter_account', '', type=str)

    # بناء الاستعلام الأساسي
    expense_query = Expense.query
    totals_query = db.session.query(
        ExpenseCategory.name,
        func.sum(Expense.amount).label('total'),
        func.count(Expense.id).label('count')
    ).join(Expense, Expense.category_id == ExpenseCategory.id)

    if date_from:
        try:
            d_from = datetime.strptime(date_from, '%Y-%m-%d')
            expense_query = expense_query.filter(Expense.date >= d_from)
            totals_query = totals_query.filter(Expense.date >= d_from)
        except: pass

    if date_to:
        try:
            d_to = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
            expense_query = expense_query.filter(Expense.date < d_to)
            totals_query = totals_query.filter(Expense.date < d_to)
        except: pass

    if filter_cat:
        expense_query = expense_query.filter(Expense.category_id == int(filter_cat))
        totals_query = totals_query.filter(Expense.category_id == int(filter_cat))

    if filter_account == 'no_account':
        expense_query = expense_query.filter(Expense.account_id == None)
        totals_query = totals_query.filter(Expense.account_id == None)
    elif filter_account:
        expense_query = expense_query.filter(Expense.account_id == int(filter_account))
        totals_query = totals_query.filter(Expense.account_id == int(filter_account))

    if filter_type:
        if filter_type == 'general':
            expense_query = expense_query.filter(Expense.is_shared == True)
            totals_query = totals_query.filter(Expense.is_shared == True)
        elif filter_type == 'withdrawal':
            expense_query = expense_query.filter(Expense.is_shared == False, Expense.description.like('سحب شخصي:%'))
            totals_query = totals_query.filter(Expense.is_shared == False, Expense.description.like('سحب شخصي:%'))
        elif filter_type == 'private_gm':
            expense_query = expense_query.filter(Expense.is_shared == False, Expense.description.like('مصروف خاص م/أحمد:%'))
            totals_query = totals_query.filter(Expense.is_shared == False, Expense.description.like('مصروف خاص م/أحمد:%'))
        elif filter_type == 'shared_50_50':
            expense_query = expense_query.filter(Expense.is_shared == False, Expense.description.like('مشترك (50/50):%'))
            totals_query = totals_query.filter(Expense.is_shared == False, Expense.description.like('مشترك (50/50):%'))
        elif filter_type == 'partners_only':
            expense_query = expense_query.filter(Expense.is_shared == False, Expense.description.like('شركاء فقط%'))
            totals_query = totals_query.filter(Expense.is_shared == False, Expense.description.like('شركاء فقط%'))
        elif filter_type == 'undefined':
            expense_query = expense_query.filter(
                or_(Expense.is_shared == False, Expense.is_shared == None),
                ~Expense.description.like('%سحب%'),
                ~Expense.description.like('%مشترك%'),
                ~Expense.description.like('%خاص%'),
                ~Expense.description.like('%شركاء فقط%')
            )
            totals_query = totals_query.filter(
                or_(Expense.is_shared == False, Expense.is_shared == None),
                ~Expense.description.like('%سحب%'),
                ~Expense.description.like('%مشترك%'),
                ~Expense.description.like('%خاص%'),
                ~Expense.description.like('%شركاء فقط%')
            )

    all_expenses = expense_query.order_by(Expense.date.desc()).all()

    # تقرير إجماليات المصروفات بالتصنيف
    category_totals = totals_query.group_by(ExpenseCategory.name)\
     .order_by(func.sum(Expense.amount).desc())\
     .all()
    
    grand_total = sum([ct.total for ct in category_totals]) if category_totals else 0

    return render_template('expenses.html', categories=categories, expenses=all_expenses, partners=partners, accounts=accounts, category_totals=category_totals, grand_total=grand_total, date_from=date_from, date_to=date_to, filter_category=filter_cat, filter_type=filter_type, filter_account=filter_account, PartnerTransaction=PartnerTransaction)

@app.route('/expenses/delete/<int:id>')
@general_manager_required
def delete_expense(id):
    try:
        exp = Expense.query.get_or_404(id)

        # 1. إرجاع الأموال للخزينة (إذا كانت الخزينة مسجلة)
        if exp.account_id:
            account = MoneyAccount.query.get(exp.account_id)
            if account:
                account.balance = round(account.balance + exp.amount, 1) # رد المبلغ

                # تسجيل حركة "استرداد" في سجل الخزينة عشان الحساب يظبط
                db.session.add(FinancialTransaction(
                    account_id=account.id,
                    type='income', # دخل (استرداد)
                    category='استرداد مصروف',
                    amount=exp.amount,
                    description=f"إلغاء مصروف: {exp.description}",
                    created_by_id=current_user.id,
                    date=cairo_now()
                ))

        # 2. حذف التأثير على الشركاء (لو كان سحب أو خاص أو مشترك أو شركاء فقط)
        # بما أننا وحدّنا طريقة كتابة الـ description، نقدر نمسح أي حركة مرتبطة بيه
        try:
            # نستخرج الوصف الأساسي بدون أي إضافات ثابتة حطيناها في الكود (عشان نمسح كل الحركات المرتبطة)
            # مثلاً "مشترك (50/50): شراء بضاعة"
            base_desc = exp.description
            
            # نمسح أي PartnerTransaction بيحتوي على نفس الوصف وفي نفس تاريخ المصروف تقريباً
            db.session.query(PartnerTransaction).filter(
                PartnerTransaction.description.like(f"%{base_desc}%"),
                db.extract('year', PartnerTransaction.date) == exp.date.year,
                db.extract('month', PartnerTransaction.date) == exp.date.month,
                db.extract('day', PartnerTransaction.date) == exp.date.day
            ).delete(synchronize_session=False)
        except Exception as e:
            print(f"Error deleting partner transactions: {e}")

        # 3. حذف المصروف
        db.session.delete(exp)
        db.session.commit()

        flash('تم حذف المصروف ورد المبلغ للخزينة وإلغاء خصم الشركاء (إن وجد) ✅', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'حدث خطأ أثناء الحذف: {e}', 'danger')

    return redirect(url_for('expenses'))


@app.route('/expenses/edit/<int:id>', methods=['POST'])
@general_manager_required
def edit_expense(id):
    try:
        exp = Expense.query.get_or_404(id)
        
        # 1. Reverse the old transaction
        if exp.account_id:
            old_account = MoneyAccount.query.get(exp.account_id)
            if old_account:
                old_account.balance = round(old_account.balance + exp.amount, 1)
                db.session.add(FinancialTransaction(
                    account_id=old_account.id, type='income', category='استرداد مصروف (تعديل)',
                    amount=exp.amount, description=f"إلغاء مصروف للتعديل: {exp.description}",
                    created_by_id=current_user.id, date=cairo_now()
                ))

        # 2. Delete old PartnerTransactions
        try:
            base_desc = exp.description
            db.session.query(PartnerTransaction).filter(
                PartnerTransaction.description.like(f"%{base_desc}%"),
                db.extract('year', PartnerTransaction.date) == exp.date.year,
                db.extract('month', PartnerTransaction.date) == exp.date.month,
                db.extract('day', PartnerTransaction.date) == exp.date.day
            ).delete(synchronize_session=False)
        except Exception as e:
            print(f"Error deleting old partner transactions during edit: {e}")

        # 3. Apply the New Details
        amount = float(request.form.get('amount'))
        description = request.form.get('description')
        category_id = request.form.get('category_id')
        expense_type = request.form.get('expense_type')
        account_id = request.form.get('account_id')
        
        exp.amount = amount
        exp.category_id = category_id
        exp.account_id = account_id
        
        # 4. Apply new PartnerTransactions based on type
        if expense_type == 'general':
            exp.is_shared = True
            exp.description = f"{description}".strip()
            
        elif expense_type == 'withdrawal':
            exp.is_shared = False
            partner_id = request.form.get('partner_id')
            if not partner_id:
                flash('يجب اختيار الشريك للمسحوبات', 'danger'); return redirect(url_for('expenses'))
            exp.description = f"سحب شخصي: {description}".strip()
            db.session.add(PartnerTransaction(
                partner_id=partner_id, type='withdrawal', amount=-amount,
                description=exp.description, date=cairo_now()
            ))
            
        elif expense_type == 'private_gm':
            exp.is_shared = False
            exp.description = f"مصروف خاص م/أحمد: {description}".strip()
            gm = User.query.filter_by(role='general_manager').first()
            if gm:
                db.session.add(PartnerTransaction(
                    partner_id=gm.id, type='expense_share', amount=-amount, 
                    description=exp.description, date=cairo_now()
                ))
                
        elif expense_type == 'shared_50_50':
            exp.is_shared = False
            exp.description = f"مشترك (50/50): {description}".strip()
            gm = User.query.filter_by(role='general_manager').first()
            partners = User.query.filter_by(role='manager').all()
            if gm and partners:
                gm_share = amount / 2
                db.session.add(PartnerTransaction(
                    partner_id=gm.id, type='expense_share', amount=-gm_share,
                    description=f"{exp.description} (حصة 50%)", date=cairo_now()
                ))
                if len(partners) > 0:
                    partner_share = gm_share / len(partners)
                    for p in partners:
                        db.session.add(PartnerTransaction(
                            partner_id=p.id, type='expense_share', amount=-partner_share,
                            description=f"{exp.description} (حصة شريك)", date=cairo_now()
                        ))
                        
        elif expense_type == 'partners_only':
            exp.is_shared = False
            exp.description = f"شركاء فقط (يوزع على 4): {description}".strip()
            partners = User.query.filter_by(role='manager').all()
            if partners and len(partners) > 0:
                partner_share = amount / len(partners)
                for p in partners:
                    db.session.add(PartnerTransaction(
                        partner_id=p.id, type='expense_share', amount=-partner_share,
                        description=f"{exp.description} (حصة شريك)", date=cairo_now()
                    ))

        # 5. Deduct new amount from Treasury
        new_account = MoneyAccount.query.get(exp.account_id)
        if new_account:
            new_account.balance -= amount
            db.session.add(FinancialTransaction(
                account_id=new_account.id, type='expense', category='مصروفات (معدل)',
                amount=-amount, description=f"صرف (تعديل): {description}", created_by_id=current_user.id, date=cairo_now()
            ))

        db.session.commit()
        flash('تم تعديل المصروف بنجاح وتحديث الحسابات ✅', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'حدث خطأ أثناء التعديل: {e}', 'danger')

    return redirect(url_for('expenses'))
