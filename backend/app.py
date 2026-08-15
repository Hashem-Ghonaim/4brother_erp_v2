import os
import json
import random
from flask import flash, session, request, redirect, url_for
from werkzeug.security import generate_password_hash
from sqlalchemy import func, text, inspect
import re
from datetime import date, datetime, timedelta
from .core import app, db, login_manager, cairo_now, basedir, FACTORY_LAT, FACTORY_LNG, ALLOWED_RADIUS, allowed_file, BASE_DIR
from .routes.auth import register_auth_routes
from .routes.treasury import register_treasury_routes

# --- Import all route modules (auto-registered via @app.route) ---
from .routes import dashboard, partners, hr, shipping, orders, invoices
from .routes import customers, suppliers, expenses, inventory, purchases
from .routes import returns, reports, settings, patterns, fixes
project_root = BASE_DIR
# Config, db, login_manager, and constants are defined in core.py

from .models import (
    SystemSetting, AttendanceSettings, User, Attendance, PatternTracking,
    Category, ProductModel, EmployeeExcuse, ProductVariant, Supplier,
    PartnerTransaction, SupplierPayment, PurchaseOrder, PurchaseItem,
    Customer, CustomerPayment, ShippingCompany, SaleOrder, SaleItem,
    FinancialTransaction, ReturnInvoice, MoneyAccount, StockMovement,
    HRTransaction, ExpenseCategory, Expense
)

#               HELPERS
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

@app.context_processor
def inject_global_vars():
    settings = SystemSetting.query.first()
    return dict(
        company_logo=settings.company_logo if settings and settings.company_logo else 'default_logo.png',
        theme_color=settings.theme_color if settings and settings.theme_color else '#0d6efd',
        active_season=session.get('active_season', 'شتوي 2027')
    )

@app.route('/set_season/<season>')
def set_season(season):
    session['active_season'] = season
    return redirect(request.referrer or url_for('dashboard'))

@app.route('/set_season_post', methods=['POST'])
def set_season_post():
    session['active_season'] = request.form.get('season')
    return redirect(request.referrer or url_for('dashboard'))

# --- Helpers (imported from helpers.py) ---
from .helpers import (general_manager_required, permission_required, permission_required_any,
                      get_accessible_users, get_allowed_customers,
                      calculate_user_commission, calculate_distance)

register_auth_routes(app)
register_treasury_routes(app, general_manager_required)

# ==============================================================================
# SEASON GLOBAL FILTERING
# ==============================================================================
from sqlalchemy.orm import Session
from sqlalchemy import event
from flask import has_request_context

SEASON_MODELS = [
    Expense, PurchaseOrder, SupplierPayment, CustomerPayment, 
    SaleOrder, ReturnInvoice, FinancialTransaction, PartnerTransaction, ProductModel, PatternTracking
]

@event.listens_for(Session, "do_orm_execute")
def _add_filtering_criteria(execute_state):
    # Apply to SELECT statements automatically including relationship loads
    if execute_state.is_select and not execute_state.is_column_load:
        if has_request_context():
            active_season = session.get('active_season', 'شتوي 2027')
            # Loop over all entities in the query
            for ext_info in execute_state.statement.column_descriptions:
                entity = ext_info.get('entity')
                if entity is not None and entity in SEASON_MODELS:
                    # Apply the global season filter
                    execute_state.statement = execute_state.statement.filter(entity.season == active_season)

def _set_active_season(mapper, connection, target):
    if has_request_context():
        # Do not override if already set explicitly to something else
        if not target.season or target.season == 'شتوي 2027' or target.season == 'صيفي 2026':
            target.season = session.get('active_season', 'شتوي 2027')

for model in SEASON_MODELS:
    event.listens_for(model, 'before_insert')(_set_active_season)

# ==============================================================================

@app.route('/setup')
def setup():
    with app.app_context():
        # 1. إنشاء كافة الجداول الجديدة التي لم تُنشأ بعد
        db.create_all()

        inspector = inspect(db.engine)
        existing_tables = inspector.get_table_names()

        # التأكد من إنشاء جدول الأذونات يدوياً إذا لم ينشئه db.create_all (للدقة)
        if 'employee_excuse' not in existing_tables:
            EmployeeExcuse.__table__.create(db.engine)

        with db.engine.connect() as conn:
            # --- تحديث جدول المستخدمين (User) بالأعمدة الناقصة ---
            cols_user = [c['name'] for c in inspector.get_columns('user')]
            if 'manager_id' not in cols_user:
                conn.execute(text("ALTER TABLE \"user\" ADD COLUMN manager_id INTEGER REFERENCES \"user\"(id)"))
            if 'shift_start' not in cols_user:
                conn.execute(text("ALTER TABLE \"user\" ADD COLUMN shift_start VARCHAR(10) DEFAULT '09:00'"))
            if 'shift_end' not in cols_user:
                conn.execute(text("ALTER TABLE \"user\" ADD COLUMN shift_end VARCHAR(10) DEFAULT '17:00'"))
            if 'permissions' not in cols_user:
                conn.execute(text("ALTER TABLE \"user\" ADD COLUMN permissions TEXT DEFAULT ''"))
            if 'commission_value' not in cols_user:
                conn.execute(text("ALTER TABLE \"user\" ADD COLUMN commission_value FLOAT DEFAULT 0.0"))
            if 'commission_rules' not in cols_user:
                conn.execute(text("ALTER TABLE \"user\" ADD COLUMN commission_rules TEXT"))

            # --- تحديث جدول المبيعات (SaleOrder) ---
            cols_order = [c['name'] for c in inspector.get_columns('sale_order')]
            if 'is_proforma' not in cols_order:
                conn.execute(text("ALTER TABLE sale_order ADD COLUMN is_proforma BOOLEAN DEFAULT false"))
            if 'shipping_notes' not in cols_order:
                conn.execute(text("ALTER TABLE sale_order ADD COLUMN shipping_notes TEXT"))
            if 'packer_id' not in cols_order:
                conn.execute(text("ALTER TABLE sale_order ADD COLUMN packer_id INTEGER"))

            # --- تحديث جدول المصروفات (Expense) ---
            cols_expense = [c['name'] for c in inspector.get_columns('expense')]
            if 'is_shared' not in cols_expense:
                conn.execute(text("ALTER TABLE expense ADD COLUMN is_shared BOOLEAN DEFAULT false"))
            if 'account_id' not in cols_expense:
                conn.execute(text("ALTER TABLE expense ADD COLUMN account_id INTEGER REFERENCES money_account(id)"))
            
            # --- تحديثات المواسم (Season Migration) ---
            tables_to_update = [
                'expense', 'purchase_order', 'supplier_payment', 'customer_payment',
                'sale_order', 'return_invoice', 'financial_transaction', 'partner_transaction', 'pattern_tracking'
            ]
            for table in tables_to_update:
                if table in existing_tables:
                    cols = [c['name'] for c in inspector.get_columns(table)]
                    if 'season' not in cols:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN season VARCHAR(50) DEFAULT 'صيفي 2026'"))
                        conn.execute(text(f"UPDATE {table} SET season = 'صيفي 2026' WHERE season IS NULL"))
                        
                        if table == 'pattern_tracking':
                            conn.execute(text("UPDATE pattern_tracking SET season = 'شتوي 2027' WHERE serial_number LIKE '%سويتشرت%' OR serial_number LIKE '%شتوي%'"))

            conn.commit()

            # --- إصلاح الحضور والانصراف من 1 أغسطس ---
            target_date = date(2026, 8, 1)
            attendances = Attendance.query.filter(Attendance.date >= target_date, Attendance.check_in != None).all()
            for att in attendances:
                user = att.user
                if not user or not user.shift_end:
                    continue
                try:
                    shift_end_t = datetime.strptime(user.shift_end, '%H:%M').time()
                    checkout_dt = datetime.combine(att.date, shift_end_t)
                    if user.shift_start:
                        shift_start_t = datetime.strptime(user.shift_start, '%H:%M').time()
                        if shift_end_t < shift_start_t:
                            checkout_dt += timedelta(days=1)
                    
                    needs_fix = False
                    if not att.check_out:
                        needs_fix = True
                    elif att.check_out < checkout_dt:
                        needs_fix = True
                        
                    if needs_fix:
                        att.check_out = checkout_dt
                except:
                    pass
            db.session.commit()

        # 2. إنشاء المدير العام (أحمد عبد الفتاح)
        gm = User.query.filter_by(username="gm_ahmed").first()
        if not gm:
            gm = User(
                fullname="أحمد عبد الفتاح",
                username="gm_ahmed",
                password=generate_password_hash("123456"),
                role="general_manager",
                emp_code="GM-001",
                phone="01067564179",
                permissions="view_reports,manage_hr,manage_inventory,manage_shipping,manage_settings,view_pos,view_invoices,manage_orders,view_treasury,manage_treasury,view_customers,manage_customers"
            )
            db.session.add(gm)
            db.session.commit()

        # 3. إنشاء المديرين الشركاء (Partners)
        managers_data = [
            {'name': 'أحمد هشام', 'user': 'mgr_hesham', 'phone': '01010893806'},
            {'name': 'أحمد وجدي', 'user': 'mgr_wagdy', 'phone': '01026520216'},
            {'name': 'أحمد أبو اليزيد', 'user': 'mgr_yazeed', 'phone': '01012253847'},
            {'name': 'أحمد العجان', 'user': 'mgr_aggan', 'phone': '01018440860'},
        ]
        managers_objs = {}
        for m in managers_data:
            user = User.query.filter_by(username=m['user']).first()
            if not user:
                user = User(
                    fullname=m['name'], username=m['user'],
                    password=generate_password_hash("123456"),
                    role="manager", emp_code=f"MGR-{random.randint(100,999)}",
                    phone=m['phone'], manager_id=gm.id,
                    permissions="view_reports,manage_shipping,view_inventory,view_pos,view_invoices,view_customers"
                )
                db.session.add(user)
                db.session.commit()
            managers_objs[m['user']] = user

        # 4. إنشاء فريق المبيعات (Sales) وتوزيعهم على المديرين
        sales_structure = {
            'mgr_hesham': [{'name': 'منار', 'user': 'sales_manar', 'phone': '01055745413'}, {'name': 'هاجر', 'user': 'sales_hager', 'phone': '01044585698'}],
            'mgr_wagdy': [{'name': 'سلمى', 'user': 'sales_salma', 'phone': '01080841802'}],
            'mgr_yazeed': [{'name': 'سماح', 'user': 'sales_samah', 'phone': '01044582182'}, {'name': 'ندى', 'user': 'sales_nada', 'phone': '01034874947'}],
            'gm_ahmed': [{'name': 'ياسمين مجدي', 'user': 'sales_yasmin', 'phone': '01040577838'}, {'name': 'أم مليكة', 'user': 'sales_omalika', 'phone': '01044585676'}, {'name': 'ريم وائل', 'user': 'sales_reem', 'phone': '01040557328'}]
        }
        for mgr_user, sales_list in sales_structure.items():
            m_id = gm.id if mgr_user == 'gm_ahmed' else (managers_objs[mgr_user].id if mgr_user in managers_objs else None)
            if not m_id: continue
            for s in sales_list:
                if not User.query.filter_by(username=s['user']).first():
                    db.session.add(User(
                        fullname=s['name'], username=s['user'], password=generate_password_hash("123456"),
                        role="sales", emp_code=f"SAL-{random.randint(100,999)}",
                        phone=s['phone'], manager_id=m_id, permissions="view_pos,view_invoices,view_customers"
                    ))

        # 5. إنشاء العمال (Workers) المشتركين
        workers_data = [
            {'name': 'يوسف', 'user': 'w_youssef', 'phone': '01050783864'},
            {'name': 'أدهم', 'user': 'w_adham', 'phone': '01080923261'},
            {'name': 'حياة', 'user': 'Hayah', 'phone': '01152512370'},
            {'name': 'مصطفى', 'user': 'w_mostafa', 'phone': '01061039810'}
        ]
        for w in workers_data:
            if not User.query.filter_by(username=w['user']).first():
                db.session.add(User(
                    fullname=w['name'], username=w['user'], password=generate_password_hash("123456"),
                    role="worker", emp_code=f"WRK-{random.randint(100,999)}",
                    phone=w['phone'], manager_id=gm.id
                ))

        # 6. تهيئة البيانات الأساسية (تصنيفات، حسابات، شحن)
        if not Category.query.first(): db.session.add(Category(name="عام"))
        if not ExpenseCategory.query.first():
            for c in ["إيجار", "رواتب", "كهرباء", "نثريات", "نقل", "تسويق"]: db.session.add(ExpenseCategory(name=c))
        if not Customer.query.filter_by(name="عميل نقدي").first():
            db.session.add(Customer(name="عميل نقدي", phone="00000000000", address="-"))
        if not ShippingCompany.query.first():
            db.session.add(ShippingCompany(name="شركة البراق", phone="010xxxx", cs_number="19xxx", fee_first_1k=50, fee_extra_1k=10))

        # إنشاء الخزائن الافتراضية
        default_accounts = ["خزنة نقدية (درج الكاش)", "فودافون كاش", "إنستا باي", "حساب بنكي", "حساب بريد"]
        for acc_name in default_accounts:
            if not MoneyAccount.query.filter_by(name=acc_name).first():
                acc_type = 'vodafone' if 'فودافون' in acc_name else ('instapay' if 'إنستا' in acc_name else ('bank' if 'بنكي' in acc_name or 'بريد' in acc_name else 'cash'))
                db.session.add(MoneyAccount(name=acc_name, balance=0.0, type=acc_type))

        db.session.commit()
        return "تم"
@app.context_processor
def inject_settings():
    # هذا الكود يجعل متغيرات الإعدادات متاحة في كل ملفات HTML تلقائياً
    setting = SystemSetting.query.first()
    if setting:
        return dict(
            global_theme_color=setting.theme_color,
            global_company_logo=setting.company_logo
        )
    return dict(global_theme_color='#0d6efd', global_company_logo=None)

@app.route('/reset_balances_danger_zone')
@general_manager_required
def reset_balances_danger_zone():
    with db.engine.connect() as conn:
        conn.execute(text('UPDATE customer SET balance = 0.0'))
        conn.execute(text('UPDATE supplier SET balance = 0.0'))
        conn.execute(text('UPDATE money_account SET balance = 0.0'))
        conn.commit()
    flash('تم تصفير جميع الحسابات بنجاح', 'success')
    return redirect(url_for('dashboard'))

