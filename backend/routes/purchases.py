"""
Routes: purchases
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


@app.route('/purchases/new', methods=['GET', 'POST'])
@login_required
def new_purchase():
    if current_user.username != 'gm_ahmed':
        flash('عفواً، ميزة إنشاء مشتريات متاحة فقط للمالك (أحمد عبد الفتاح).', 'danger')
        return redirect(url_for('inventory'))
    if request.method == 'POST':
        supplier_id = request.form.get('supplier_id')

        # استقبال البيانات من الفورم كقوائم
        product_ids = request.form.getlist('product_id[]') # الحقل المخفي للـ ID
        names = request.form.getlist('name[]')
        costs = request.form.getlist('cost[]')
        sells = request.form.getlist('sell[]')
        qtys = request.form.getlist('qty[]')
        barcodes = request.form.getlist('barcode[]')
        categories = request.form.getlist('category[]')
        images = request.files.getlist('image[]')
        seasons = request.form.getlist('season[]')

        new_supp_name = request.form.get('new_supplier_name')
        new_supp_phone = request.form.get('new_supplier_phone')

        if not names:
            flash('لم يتم إدخال أصناف!', 'warning')
            return redirect(request.url)

        # 1. معالجة المورد (جديد أو موجود)
        if supplier_id == 'new' and new_supp_name:
            new_supp = Supplier(name=new_supp_name, phone=new_supp_phone)
            db.session.add(new_supp)
            db.session.flush()
            supplier = new_supp
        elif supplier_id and supplier_id != 'new':
            supplier = Supplier.query.get(supplier_id)
        else:
            supplier = None

        # 2. إنشاء رأس الفاتورة
        purchase_order = PurchaseOrder(created_by=current_user.id, total_cost=0.0, status='received')
        if supplier:
            purchase_order.supplier_id = supplier.id
        db.session.add(purchase_order)
        db.session.flush()

        total_invoice_cost = 0.0

        # 3. اللفة على المنتجات (Product Loop)
        for i in range(len(names)):
            p_name = names[i].strip()
            if not p_name: continue

            # معالجة الأرقام لتجنب أخطاء الإدخال
            try: cost = float(costs[i]) if costs[i].strip() else 0.0
            except: cost = 0.0
            try: sell = float(sells[i]) if sells[i].strip() else 0.0
            except: sell = 0.0
            try: qty = int(qtys[i]) if qtys[i].strip() else 0
            except: qty = 0

            p_id = product_ids[i] if i < len(product_ids) else ""
            p_barcode = barcodes[i].strip() if i < len(barcodes) else None
            if p_barcode == "": p_barcode = None

            p_category = categories[i].strip() if i < len(categories) else "عام"
            if not p_category: p_category = "عام"

            p_season = seasons[i].strip() if i < len(seasons) else "شتوي 2027"
            if not p_season: p_season = "شتوي 2027"

            # معالجة الصورة المرفوعة
            image_filename = 'default_product.png'
            if i < len(images):
                file = images[i]
                if file and file.filename != '':
                    filename = secure_filename(file.filename)
                    # إضافة طابع زمني لاسم الصورة لمنع التداخل
                    filename = f"{int(cairo_now().timestamp())}_{filename}"
                    from backend.supabase_storage import upload_file_to_supabase
                    success, url = upload_file_to_supabase(file, filename, app.config)
                    if success:
                        image_filename = url
                    else:
                        image_filename = 'default_product.png'

            # أ) تحديد الكاتيجوري أولاً
            cat = Category.query.filter_by(name=p_category).first()
            if not cat:
                cat = Category(name=p_category)
                db.session.add(cat)
                db.session.flush()

            variant = None

            # ب) المنطق المطور للبحث (الاسم + الفئة)
            # 1. إذا كان هناك ID مرسل من الفورم (المستخدم اختار منتج موجود ولم يغير فئته)
            if p_id and p_id != "":
                variant = ProductVariant.query.get(p_id)

            # 2. إذا لم نجد بـ ID، نبحث بالباركود (لأنه فريد)
            if not variant and p_barcode:
                variant = ProductVariant.query.filter_by(barcode=p_barcode).first()

            # 3. إذا لم نجد، نبحث بالاسم داخل هذا التصنيف تحديداً
            if not variant:
                model = ProductModel.query.filter_by(name=p_name, category_id=cat.id).first()
                if model:
                    if model.variants:
                        variant = model.variants[0]
                    # تحديث الصورة لو تم رفع صورة جديدة لمنتج موجود
                    if image_filename != 'default_product.png':
                        model.image = image_filename
                    model.season = p_season
                else:
                    # ج) إنشاء منتج جديد تماماً (موديل + فارينت)
                    model = ProductModel(name=p_name, category_id=cat.id, image=image_filename, season=p_season)
                    db.session.add(model)
                    db.session.flush()

                    variant = ProductVariant(model_id=model.id, barcode=p_barcode, cost_price=cost, sell_price=sell, stock=0)
                    db.session.add(variant)
                    db.session.flush()

            # د) تحديث بيانات المخزون والأسعار
            if cost > 0: variant.cost_price = cost
            if sell > 0: variant.sell_price = sell
            variant.stock += qty

            # تسجيل حركة المخزون
            db.session.add(StockMovement(
                variant_id=variant.id,
                user_id=current_user.id,
                quantity_change=qty,
                reason=f"شراء فاتورة #{purchase_order.id}"
            ))

            # حساب إجمالي الفاتورة
            item_total = cost * qty
            total_invoice_cost += item_total

            db.session.add(PurchaseItem(
                purchase_id=purchase_order.id,
                variant_id=variant.id,
                quantity=qty,
                unit_cost=cost,
                total_cost=item_total
            ))

        # 4. تحديث إجماليات الفاتورة وحساب المورد
        purchase_order.total_cost = total_invoice_cost
        if supplier:
            supplier.balance += total_invoice_cost

        db.session.commit()

        flash(f'تم حفظ الفاتورة بنجاح ✅ (إجمالي: {total_invoice_cost} ج.م)', 'success')
        return redirect(url_for('inventory'))

    # عرض الصفحة (GET)
    return render_template('new_purchase.html',
                           suppliers=Supplier.query.all(),
                           categories=Category.query.all(),
                           product_suggestions=ProductVariant.query.all())
    # =========================================================

@app.route('/purchases/edit/<int:id>', methods=['GET', 'POST'])
@permission_required('manage_inventory')
def edit_purchase(id):
    order = PurchaseOrder.query.get_or_404(id)

    if request.method == 'POST':
        try:
            # 1. عكس التأثير القديم (إرجاع المخزون + إلغاء دين المورد)
            old_total_cost = order.total_cost
            if order.supplier:
                order.supplier.balance -= old_total_cost  # إلغاء الدين القديم

            for item in order.items:
                if item.variant:
                    item.variant.stock -= item.quantity  # سحب الكمية التي أضيفت سابقاً
                    # تسجيل حركة مخزون عكسية
                    db.session.add(StockMovement(
                        variant_id=item.variant.id,
                        user_id=current_user.id,
                        quantity_change=-item.quantity,
                        reason=f"تعديل فاتورة شراء #{order.id} (تصحيح)"
                    ))

            # حذف الأصناف القديمة
            PurchaseItem.query.filter_by(purchase_id=order.id).delete()

            # 2. إضافة الأصناف الجديدة (نفس منطق الشراء الجديد)
            product_ids = request.form.getlist('product_id[]')
            names = request.form.getlist('name[]')
            costs = request.form.getlist('cost[]')
            sells = request.form.getlist('sell[]')
            qtys = request.form.getlist('qty[]')
            barcodes = request.form.getlist('barcode[]')
            categories = request.form.getlist('category[]')
            images = request.files.getlist('image[]')
            seasons = request.form.getlist('season[]')

            # تحديث المورد لو تغير
            new_supplier_id = request.form.get('supplier_id')
            if new_supplier_id and new_supplier_id != 'new':
                order.supplier_id = new_supplier_id
            elif new_supplier_id == 'new':
                new_supp_name = request.form.get('new_supplier_name')
                new_supp_phone = request.form.get('new_supplier_phone')
                if new_supp_name:
                    new_supp = Supplier(name=new_supp_name, phone=new_supp_phone)
                    db.session.add(new_supp)
                    db.session.flush()
                    order.supplier_id = new_supp.id

            new_total_cost = 0.0

            for i in range(len(names)):
                p_name = names[i].strip()
                if not p_name: continue

                try: cost = float(costs[i]) if costs[i].strip() else 0.0
                except: cost = 0.0
                try: sell = float(sells[i]) if sells[i].strip() else 0.0
                except: sell = 0.0
                try: qty = int(qtys[i]) if qtys[i].strip() else 0
                except: qty = 0

                if qty <= 0: continue

                p_id = product_ids[i] if i < len(product_ids) else ""
                p_barcode = barcodes[i].strip() if i < len(barcodes) else None
                if p_barcode == "": p_barcode = None

                p_category = categories[i].strip() if i < len(categories) else "عام"
                if not p_category: p_category = "عام"

                p_season = seasons[i].strip() if i < len(seasons) else "شتوي 2027"
                if not p_season: p_season = "شتوي 2027"

                # معالجة الصورة المرفوعة
                image_filename = 'default_product.png'
                if i < len(images):
                    file = images[i]
                    if file and file.filename != '':
                        filename = secure_filename(file.filename)
                        filename = f"{int(cairo_now().timestamp())}_{filename}"
                        from backend.supabase_storage import upload_file_to_supabase
                        success, url = upload_file_to_supabase(file, filename, app.config)
                        if success:
                            image_filename = url

                # أ) تحديد الكاتيجوري
                cat = Category.query.filter_by(name=p_category).first()
                if not cat:
                    cat = Category(name=p_category)
                    db.session.add(cat)
                    db.session.flush()

                variant = None

                # ب) مطابقة المنتج (ID ثم باركود ثم اسم)
                if p_id and p_id != "":
                    variant = ProductVariant.query.get(p_id)

                if not variant and p_barcode:
                    variant = ProductVariant.query.filter_by(barcode=p_barcode).first()

                if not variant:
                    model = ProductModel.query.filter_by(name=p_name, category_id=cat.id).first()
                    if model:
                        if model.variants:
                            variant = model.variants[0]
                        if image_filename != 'default_product.png':
                            model.image = image_filename
                        model.season = p_season
                    else:
                        # ج) إنشاء منتج جديد تماماً
                        model = ProductModel(name=p_name, category_id=cat.id, image=image_filename, season=p_season)
                        db.session.add(model)
                        db.session.flush()

                        variant = ProductVariant(model_id=model.id, barcode=p_barcode, cost_price=cost, sell_price=sell, stock=0)
                        db.session.add(variant)
                        db.session.flush()

                # د) تحديث بيانات المخزون والأسعار
                if cost > 0: variant.cost_price = cost
                if sell > 0: variant.sell_price = sell
                variant.stock += qty

                # تسجيل حركة المخزون إضافة
                db.session.add(StockMovement(
                    variant_id=variant.id,
                    user_id=current_user.id,
                    quantity_change=qty,
                    reason=f"تعديل فاتورة شراء #{order.id} (إضافة مستحدثة)"
                ))

                item_total = cost * qty
                new_total_cost += item_total

                db.session.add(PurchaseItem(
                    purchase_id=order.id,
                    variant_id=variant.id,
                    quantity=qty,
                    unit_cost=cost,
                    total_cost=item_total
                ))

            # 3. تحديث إجماليات الفاتورة وحساب المورد
            order.total_cost = new_total_cost
            if order.supplier:
                order.supplier.balance += new_total_cost # إضافة الدين الجديد

            db.session.commit()
            flash(f'تم تعديل فاتورة الشراء بنجاح ✅ (الإجمالي الجديد: {new_total_cost})', 'success')
            return redirect(url_for('purchase_details', id=order.id))

        except Exception as e:
            db.session.rollback()
            flash(f'حدث خطأ: {e}', 'danger')
            return redirect(request.url)

    return render_template('edit_purchase.html', 
                            order=order, 
                            suppliers=Supplier.query.all(),
                            categories=Category.query.all(),
                            product_suggestions=ProductVariant.query.all())
# ===   نظام مسير الرواتب (Payroll)    ===

@app.route('/purchases/<int:id>')
@permission_required('manage_inventory')
def purchase_details(id): return render_template('purchase_details.html', order=PurchaseOrder.query.get_or_404(id))


@app.route('/purchases/return', methods=['GET', 'POST'])
@login_required
def purchase_return():
    if request.method == 'POST':
        supplier_id = request.form.get('supplier_id')
        names = request.form.getlist('name[]')
        product_ids = request.form.getlist('product_id[]')
        costs = request.form.getlist('cost[]')
        qtys = request.form.getlist('qty[]')

        if not product_ids:
            flash('لم يتم اختيار أصناف للمرتجع!', 'warning')
            return redirect(request.url)

        supplier = Supplier.query.get(supplier_id)
        total_return_value = 0.0

        for i in range(len(product_ids)):
            p_id = product_ids[i]
            if not p_id: continue

            try:
                qty = int(qtys[i])
                cost = float(costs[i])
            except: continue

            variant = ProductVariant.query.get(p_id)
            if variant:
                # 1. خصم الكمية من المخزن
                variant.stock -= qty

                # 2. تسجيل حركة المخزن (بالسالب)
                db.session.add(StockMovement(
                    variant_id=variant.id,
                    user_id=current_user.id,
                    quantity_change=-qty,
                    reason=f"مرتجع شراء للمورد: {supplier.name}"
                ))

                total_return_value += (cost * qty)

        # 3. خصم إجمالي المرتجع من حساب المورد (تقليل المديونية)
        if supplier:
            supplier.balance -= total_return_value

        db.session.commit()
        flash(f'تم تسجيل المرتجع بنجاح ✅ وخصم {total_return_value} من حساب المورد', 'success')
        return redirect(url_for('supplier_profile', supplier_id=supplier.id))

    # في حالة الـ GET (عرض الصفحة)
    return render_template('new_purchase_return.html',
                           suppliers=Supplier.query.all(),
                           product_suggestions=ProductVariant.query.all())
