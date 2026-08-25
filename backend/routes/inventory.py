"""
Routes: inventory
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


@app.route('/public/catalog')
def public_catalog():
    ref = request.args.get('ref')
    if not ref:
        return "Ø¹ÙÙˆØ§Ù‹ØŒ Ù„Ø§ ÙŠÙ…ÙƒÙ†Ùƒ Ø§Ù„ÙˆØµÙˆÙ„ Ø¥Ù„Ù‰ Ù‡Ø°Ø§ Ø§Ù„Ø±Ø§Ø¨Ø· Ù…Ø¨Ø§Ø´Ø±Ø©. ÙŠØ±Ø¬Ù‰ Ø§Ù„Ø­ØµÙˆÙ„ Ø¹Ù„Ù‰ Ø±Ø§Ø¨Ø· Ø¯Ø¹ÙˆØ© Ù…Ù† Ø£Ø­Ø¯ Ù…Ù…Ø«Ù„ÙŠ Ø§Ù„Ù…Ø¨ÙŠØ¹Ø§Øª Ø§Ù„Ø®Ø§Øµ Ø¨Ù†Ø§.", 403

    # Ø¬Ù„Ø¨ ÙƒÙ„ Ø§Ù„ØªØµÙ†ÙŠÙØ§Øª
    all_cats = Category.query.all()
    catalog_data = []

    for cat in all_cats:
        # Ø¬Ù„Ø¨ Ø§Ù„Ù…Ù†ØªØ¬Ø§Øª Ø§Ù„Ù…ØªØ§Ø­Ø© ÙÙ‚Ø· (Ø±ØµÙŠØ¯ > 0) Ø§Ù„ØªØ§Ø¨Ø¹Ø© Ù„Ù‡Ø°Ø§ Ø§Ù„ØªØµÙ†ÙŠÙ
        # Ù†Ø³ØªØ®Ø¯Ù… join Ù„Ø£Ù† Ø§Ù„Ù€ category_id Ù…ÙˆØ¬ÙˆØ¯ ÙÙŠ ProductModel ÙˆÙ„ÙŠØ³ Variant
        products = ProductVariant.query.join(ProductModel).filter(
            ProductModel.category_id == cat.id,
            ProductVariant.stock > 0
        ).all()

        # Ø¥Ø°Ø§ ÙƒØ§Ù† Ø§Ù„ØªØµÙ†ÙŠÙ ÙŠØ­ØªÙˆÙŠ Ø¹Ù„Ù‰ Ù…Ù†ØªØ¬Ø§Øª Ù…ØªØ§Ø­Ø©ØŒ Ù†Ø¶ÙŠÙÙ‡ Ù„Ù„Ù‚Ø§Ø¦Ù…Ø©
        if products:
            catalog_data.append({
                'category': cat,
                'products': products
            })

    return render_template('public_catalog.html',
                           catalog_data=catalog_data,
                           company_name= "Ù…ØµÙ†Ø¹ ÙÙˆØ± Ø¨Ø±Ø§Ø²Ø±")

@app.route('/api/update_product_image', methods=['POST'])
@permission_required('manage_inventory')
def update_product_image():
    try:
        product_id = request.form.get('id')
        if 'image' not in request.files:
            return jsonify({'success': False, 'message': 'Ù„Ù… ÙŠØªÙ… Ø§Ø®ØªÙŠØ§Ø± ØµÙˆØ±Ø©'}), 400
        
        file = request.files['image']
        if file.filename == '':
            return jsonify({'success': False, 'message': 'Ø§Ø³Ù… Ø§Ù„Ù…Ù„Ù ÙØ§Ø±Øº'}), 400

        if file:
            filename = secure_filename(file.filename)
            filename = f"{int(cairo_now().timestamp())}_{filename}"
            from backend.supabase_storage import upload_file_to_supabase
            success, url = upload_file_to_supabase(file, filename, app.config)
            
            if success:
                # ØªØ­Ø¯ÙŠØ« Ø§Ù„Ù…Ù†ØªØ¬
                variant = ProductVariant.query.get(product_id)
                if variant:
                    variant.model.image = url
                    db.session.commit()
                    return jsonify({'success': True, 'message': 'ØªÙ… ØªØ­Ø¯ÙŠØ« Ø§Ù„ØµÙˆØ±Ø© Ø¨Ù†Ø¬Ø§Ø­', 'image_url': url})
                else:
                    return jsonify({'success': False, 'message': 'Ø§Ù„Ù…Ù†ØªØ¬ ØºÙŠØ± Ù…ÙˆØ¬ÙˆØ¯'}), 404
            else:
                return jsonify({'success': False, 'message': 'ÙØ´Ù„ Ø±ÙØ¹ Ø§Ù„ØµÙˆØ±Ø©'}), 500

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/inventory')
@permission_required_any('manage_inventory', 'print_barcode', 'view_inventory')
def inventory(): return render_template('inventory.html', products=ProductVariant.query.join(ProductModel).filter(ProductModel.season == session.get('active_season', 'Ø´ØªÙˆÙŠ 2027')).order_by(ProductVariant.id).all(), user=current_user, categories=Category.query.all())


@app.route('/verify_password_api', methods=['POST'])
@login_required
def verify_password_api():
    data = request.get_json()
    password = data.get('password', '')
    if check_password_hash(current_user.password, password):
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': 'ÙƒÙ„Ù…Ø© Ø§Ù„Ø³Ø± ØºÙ„Ø·'}), 401


@app.route('/product/edit/<int:id>', methods=['POST'])
@permission_required('manage_inventory')
def edit_product(id):
    var = ProductVariant.query.get_or_404(id)
    var.model.name = request.form['name']; var.cost_price = float(request.form['cost']); var.sell_price = float(request.form['sell'])
    if 'season' in request.form: var.model.season = request.form['season']
    new_stock = int(request.form['stock']); diff = new_stock - var.stock
    # ØªØ­Ø¯ÙŠØ« Ø§Ù„ØµÙˆØ±Ø© Ù„Ùˆ Ù…ÙˆØ¬ÙˆØ¯Ø©
    if 'image' in request.files:
        file = request.files['image']
        if file and file.filename != '':
            filename = secure_filename(file.filename)
            from backend.supabase_storage import upload_file_to_supabase
            success, url = upload_file_to_supabase(file, filename, app.config)
            if success:
                var.model.image = url
    if diff != 0: var.stock = new_stock; db.session.add(StockMovement(variant_id=var.id, user_id=current_user.id, quantity_change=diff, reason="ØªØ¹Ø¯ÙŠÙ„ ÙŠØ¯ÙˆÙŠ"))
    db.session.commit(); return redirect(url_for('inventory'))


@app.route('/product/tracking')
@login_required
@permission_required('view_inventory')
def product_tracking():
    variant_id = request.args.get('variant_id', type=int)
    exact_variant_id = request.args.get('exact_variant_id', type=int)
    
    variant = None
    history = []
    total_in = 0
    total_out = 0
    
    # Ù†Ø¬ÙŠØ¨ ÙƒÙ„ Ø§Ù„Ø£ØµÙ†Ø§Ù Ø¹Ø´Ø§Ù† Ø§Ù„Ù€ dropdown ÙÙŠ Ø§Ù„Ø¨Ø­Ø«
    all_variants = ProductVariant.query.join(ProductModel).join(Category).order_by(Category.name).all()

    # Ù„Ùˆ Ø¨ÙŠØ¨Ø­Ø« Ø¨Ø±Ù‚Ù… Ø§Ù„ØµÙ†Ù (ID) Ø§Ù„Ù…Ø·Ø§Ø¨Ù‚ Ø£ÙˆÙ„Ø§Ù‹
    if exact_variant_id:
        variant = ProductVariant.query.get(exact_variant_id)
        if not variant:
            flash('Ø±Ù‚Ù… Ø§Ù„ØµÙ†Ù (ID) ØºÙŠØ± Ù…ÙˆØ¬ÙˆØ¯ ÙÙŠ Ø§Ù„Ù†Ø¸Ø§Ù…!', 'warning')
    # Ù„Ùˆ Ø§Ø®ØªØ§Ø± Ù…Ù† Ø§Ù„Ù‚Ø§Ø¦Ù…Ø©
    elif variant_id:
        variant = ProductVariant.query.get_or_404(variant_id)
        
    if variant:
        # Ø¬Ù„Ø¨ Ø§Ù„Ø­Ø±ÙƒØ§Øª ÙƒÙ„Ù‡Ø§ Ù„Ù„ØµÙ†Ù Ø¯Ù‡ Ù…ØªØ±ØªØ¨Ø© Ù…Ù† Ø§Ù„Ø£Ù‚Ø¯Ù… Ù„Ù„Ø£Ø­Ø¯Ø« Ø¹Ø´Ø§Ù† Ù†Ø­Ø³Ø¨ Ø§Ù„Ø±ØµÙŠØ¯ Ø§Ù„ØªØ±Ø§ÙƒÙ…ÙŠ
        movements = StockMovement.query.filter_by(variant_id=variant.id).order_by(StockMovement.timestamp.asc(), StockMovement.id.asc()).all()
        
        running_balance = 0
        for mov in movements:
            running_balance += mov.quantity_change
            
            if mov.quantity_change > 0:
                total_in += mov.quantity_change
            elif mov.quantity_change < 0:
                total_out += abs(mov.quantity_change)
                
            # Ø§Ø³ØªØ®Ø±Ø§Ø¬ Ø±Ø§Ø¨Ø· Ø§Ù„ÙØ§ØªÙˆØ±Ø© Ù…Ù† Ø§Ù„ÙˆØµÙ (Ù„Ùˆ Ù…ÙˆØ¬ÙˆØ¯)
            url = None
            reason = mov.reason

            
            # Ø¨ÙŠØ¹ ÙØ§ØªÙˆØ±Ø© #id
            sale_match = re.search(r'Ø¨ÙŠØ¹ ÙØ§ØªÙˆØ±Ø© #(\d+)', reason)
            if sale_match:
                url = url_for('print_invoice', id=int(sale_match.group(1)))
                
            # Ø´Ø±Ø§Ø¡ ÙØ§ØªÙˆØ±Ø© #id
            purchase_match = re.search(r'ÙØ§ØªÙˆØ±Ø© Ø´Ø±Ø§Ø¡ #(\d+)', reason)
            if purchase_match:
                url = url_for('purchase_details', id=int(purchase_match.group(1)))
                
            # Ù…Ø±ØªØ¬Ø¹ ÙØ§ØªÙˆØ±Ø© #id
            return_match = re.search(r'Ù…Ø±ØªØ¬Ø¹ ÙØ§ØªÙˆØ±Ø© #(\d+)', reason)
            if return_match:
                url = url_for('returns_list') # Ø£Ùˆ Ø±Ø§Ø¨Ø· ØªÙØ§ØµÙŠÙ„ Ø§Ù„Ù…Ø±ØªØ¬Ø¹ Ù„Ùˆ Ù…ÙˆØ¬ÙˆØ¯
                
            history.insert(0, {
                'id': mov.id,
                'date': mov.timestamp,
                'reason': mov.reason,
                'user': User.query.get(mov.user_id).fullname if mov.user_id else 'Ù†Ø¸Ø§Ù…',
                'change': mov.quantity_change,
                'balance_after': running_balance,
                'url': url
            })
            
    return render_template('product_tracking.html', variant=variant, history=history, all_variants=all_variants, total_in=total_in, total_out=total_out, exact_variant_id=exact_variant_id)

@app.route('/product/delete/<int:id>')
@permission_required('manage_inventory')
def delete_product(id):
    try: var = ProductVariant.query.get_or_404(id); db.session.delete(var.model); db.session.delete(var); db.session.commit()
    except: pass
    return redirect(url_for('inventory'))


@app.route('/print_barcode/<int:id>')
@login_required
@permission_required_any('manage_inventory', 'print_barcode')
def print_barcode(id): return render_template('print_barcode.html', product=ProductVariant.query.get_or_404(id))


@app.route('/api/quick_update_product', methods=['POST'])
@permission_required('manage_inventory')
def quick_update_product():
    data = request.get_json()
    product_id = data.get('id')
    field = data.get('field') # name, cost_price, sell_price, stock
    value = data.get('value')

    variant = ProductVariant.query.get(product_id)
    if not variant:
        return jsonify({'success': False, 'message': 'Ø§Ù„Ù…Ù†ØªØ¬ ØºÙŠØ± Ù…ÙˆØ¬ÙˆØ¯'}), 404

    try:
        if field == 'name':
            variant.model.name = value
        elif field == 'season':
            variant.model.season = value
        elif field == 'cost':
            variant.cost_price = float(value)
        elif field == 'sell':
            variant.sell_price = float(value)
        elif field == 'stock':
            old_stock = variant.stock
            new_stock = int(value)
            diff = new_stock - old_stock
            if diff != 0:
                variant.stock = new_stock
                # ØªØ³Ø¬ÙŠÙ„ Ø­Ø±ÙƒØ© Ù…Ø®Ø²ÙˆÙ† Ù„Ù„ØªØ¹Ø¯ÙŠÙ„ Ø§Ù„ÙŠØ¯ÙˆÙŠ
                db.session.add(StockMovement(
                    variant_id=variant.id,
                    user_id=current_user.id,
                    quantity_change=diff,
                    reason="ØªØ¹Ø¯ÙŠÙ„ Ø³Ø±ÙŠØ¹ Ù…Ù† Ø§Ù„Ø¬Ø¯ÙˆÙ„"
                ))

        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/inventory/print_catalog')
@login_required  # Ù…ØªØ§Ø­ Ù„Ø£ÙŠ Ø´Ø®Øµ Ù…Ø³Ø¬Ù„ Ø¯Ø®ÙˆÙ„
def print_inventory_catalog():
    # Ø§Ø³ØªÙ‚Ø¨Ø§Ù„ Ø§Ù„ØªØµÙ†ÙŠÙ Ù…Ù† Ø§Ù„Ø±Ø§Ø¨Ø·
    cat_id = request.args.get('category_id')

    # Ø§Ù„Ø§Ø³ØªØ¹Ù„Ø§Ù… Ø§Ù„Ø£Ø³Ø§Ø³ÙŠ: ØªØ±ØªÙŠØ¨ Ø¨Ø§Ù„ÙƒÙˆØ¯ + (Ø´Ø±Ø· Ø§Ù„Ù…Ø®Ø²ÙˆÙ† Ø£ÙƒØ¨Ø± Ù…Ù† ØµÙØ±)
    query = ProductVariant.query.filter(ProductVariant.stock > 0).join(ProductModel).order_by(ProductVariant.id)

    title_text = "ÙƒÙ„ Ø§Ù„Ù…Ù†ØªØ¬Ø§Øª Ø§Ù„Ù…ØªÙˆÙØ±Ø©"

    # ØªØ·Ø¨ÙŠÙ‚ ÙÙ„ØªØ± Ø§Ù„ØªØµÙ†ÙŠÙ
    if cat_id and cat_id != 'all':
        query = query.filter(ProductModel.category_id == cat_id)
        category = Category.query.get(cat_id)
        if category:
            title_text = category.name

    products = query.all()

    return render_template('print_catalog.html', products=products, catalog_title=title_text)

@app.route('/api/search_product')
def search_product():
    q = request.args.get('q', '')
    # Ø¨Ù†Ø¬ÙŠØ¨ Ø§Ù„Ù…Ù†ØªØ¬ Ù…Ø¹ Ø§Ù„ØªØµÙ†ÙŠÙ Ø¨ØªØ§Ø¹Ù‡ Ø¹Ø´Ø§Ù† Ù†Ø¹Ø±Ø¶Ù‡
    products = ProductModel.query.join(Category).filter(ProductModel.name.ilike(f'%{q}%')).limit(20).all()
    results = []
    for p in products:
        results.append({
            'id': p.id,
            'name': p.name,       # Ø§Ù„Ø§Ø³Ù… Ø§Ù„ØµØ§ÙÙŠ
            'category_id': p.category_id,
            'category_name': p.category.name,
            # Ø¯Ø§ Ø§Ù„Ù„ÙŠ Ù‡ÙŠØ¸Ù‡Ø±Ù„Ùƒ ÙÙŠ Ø§Ù„Ù‚Ø§Ø¦Ù…Ø©: "Ø§Ø³Ù… Ø§Ù„Ù…Ù†ØªØ¬ (Ø§Ø³Ù… Ø§Ù„ØªØµÙ†ÙŠÙ)"
            'label': f"{p.name} - ({p.category.name})"
        })
    return jsonify(results)

