"""
Routes: settings
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


@app.route('/settings', methods=['GET', 'POST'])
@login_required # أو @general_manager_required حسب نظامك
def settings():
    # جلب الإعدادات الحالية أو إنشاء صف جديد إذا لم يوجد
    setting = SystemSetting.query.first()
    if not setting:
        setting = SystemSetting()
        db.session.add(setting)
        db.session.commit()

    if request.method == 'POST':
        try:
            # 1. تحديث اللون
            new_color = request.form.get('theme_color')
            if new_color:
                setting.theme_color = new_color

            # 2. معالجة رفع الشعار (Logo)
            if 'company_logo' in request.files:
                file = request.files['company_logo']
                if file and file.filename != '':
                    filename = secure_filename(file.filename)
                    filename = f"{int(cairo_now().timestamp())}_{filename}"
                    from backend.supabase_storage import upload_file_to_supabase
                    success, url = upload_file_to_supabase(file, filename, app.config)
                    if success:
                        setting.company_logo = url

            db.session.commit()
            flash('تم حفظ إعدادات النظام وتحديث المظهر بنجاح ✅', 'success')

        except Exception as e:
            db.session.rollback()
            flash(f'حدث خطأ أثناء الحفظ: {str(e)}', 'danger')

        return redirect(url_for('settings'))

    # في حالة GET نعرض الصفحة بالبيانات الحالية
    return render_template('settings.html',
                         theme_color=setting.theme_color,
                         company_logo=setting.company_logo)
