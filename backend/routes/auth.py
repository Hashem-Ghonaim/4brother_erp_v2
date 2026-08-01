import os

from flask import render_template, request, redirect, url_for, flash
from flask_login import login_user, login_required, logout_user, current_user
from werkzeug.security import check_password_hash

from ..core import basedir, db
from ..models import User


def register_auth_routes(app):
    @app.route('/permissions', methods=['GET', 'POST'])
    @login_required
    def manage_permissions():
        if current_user.role != 'general_manager':
            return "غير مصرح لك", 403

        if request.method == 'POST':
            user_id = request.form.get('user_id')
            user = User.query.get(user_id)
            if user:
                perms = request.form.getlist('perms')
                user.permissions = ",".join(perms)
                db.session.commit()
                flash(f'تم تحديث صلاحيات {user.fullname} بنجاح', 'success')
            return redirect(url_for('manage_permissions'))

        users = User.query.filter(User.role != 'general_manager').all()

        # Group employees by their actual manager
        # Find all unique managers who have subordinates in this list
        manager_ids = set(u.manager_id for u in users if u.manager_id)
        # Only include managers who are actual "manager" role (not the employees themselves being grouped)
        all_managers = User.query.filter(User.id.in_(manager_ids)).all() if manager_ids else []

        manager_groups = []
        assigned_ids = set()

        for mgr in all_managers:
            subs = [u for u in users if u.manager_id == mgr.id and u.id != mgr.id]
            if subs:
                manager_groups.append({'manager': mgr, 'employees': subs})
                assigned_ids.update(u.id for u in subs)

        # Unassigned employees (no manager_id)
        unassigned = [u for u in users if u.id not in assigned_ids and u.id not in [m.id for m in all_managers]]
        if unassigned:
            manager_groups.append({'manager': None, 'employees': unassigned})

        system_permissions = {
            'المخزون': [('view_inventory', 'رؤية المخزون'), ('manage_inventory', 'تعديل المخزون (إضافة/حذف)'), ('print_barcode', 'طباعة باركود وتقارير المخزن')],
            'الشحن': [('view_shipping', 'رؤية الشحن'), ('manage_shipping', 'إدارة الشحن (تغيير حالة)')],
            'المبيعات': [('view_invoices', 'سجل الفواتير'), ('manage_orders', 'حذف/تعديل الفواتير')],
            'الخزينة': [('view_treasury', 'رؤية الخزينة'), ('manage_treasury', 'إدارة الأموال')],
            'العملاء': [('view_customers', 'رؤية العملاء'), ('manage_customers', 'إضافة/تعديل عملاء')],
            'متابعة القصات': [('view_patterns', 'رؤية القصات'), ('manage_patterns', 'إضافة وتعديل القصات')],
        }
        return render_template('permissions.html', users=users, system_perms=system_permissions, manager_groups=manager_groups)

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            user = User.query.filter_by(username=request.form['username']).first()
            if user and check_password_hash(user.password, request.form['password']):
                login_user(user)
                return redirect(url_for('dashboard'))
            flash('خطأ في اسم المستخدم أو كلمة المرور')

        fast_access_enabled = os.path.exists(os.path.join(basedir, '.fastaccess'))
        users_list = User.query.all() if fast_access_enabled else []
        return render_template('login.html', fast_access=fast_access_enabled, users_list=users_list)

    @app.route('/fast_login/<int:user_id>')
    def fast_login(user_id):
        if not os.path.exists(os.path.join(basedir, '.fastaccess')):
            flash('الدخول السريع معطل حالياً')
            return redirect(url_for('login'))
        user = User.query.get_or_404(user_id)
        login_user(user)
        return redirect(url_for('dashboard'))

    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        return redirect(url_for('login'))
