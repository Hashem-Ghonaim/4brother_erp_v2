"""
Routes: hr
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

from ..core import app, db, cairo_now, basedir, FACTORY_LAT, FACTORY_LNG, ALLOWED_RADIUS, allowed_file, BASE_DIR, SEASON_START, SEASON_END
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


def fill_missing_attendances(month_str):
    """
    تقوم هذه الدالة بإنشاء سجلات 'غياب' للأيام التي لم يتم تسجيل حضور فيها
    عن طريق مقارنة كل الأيام من بداية الشهر حتى اليوم بجدول الحضور.
    """
    try:
        # تحديد بداية ونهاية الفترة المراد فحصها
        start_date = datetime.strptime(f"{month_str}-01", "%Y-%m-%d").date()
        
        # نهاية الفترة إما آخر يوم في الشهر أو اليوم الحالي (لا نسجل غياب لأيام لسه مجاتش)
        if start_date.month == date.today().month and start_date.year == date.today().year:
            end_date = date.today()
        else:
            # نجيب آخر يوم في الشهر
            # (طريقة بسيطة: نروح لأول يوم في الشهر اللي بعده ونطرح يوم)
            if start_date.month == 12:
                next_month = start_date.replace(year=start_date.year + 1, month=1)
            else:
                next_month = start_date.replace(month=start_date.month + 1)
            end_date = next_month - timedelta(days=1)
            
            # برضه بنتأكد إننا متجاوزناش تاريخ اليوم عموماً لو الشهر ده في الماضي
            if end_date > date.today():
                end_date = date.today()

        # جلب الإعدادات لمعرفة أيام الإجازات (التي يجب تجاهلها)
        settings = AttendanceSettings.query.first()
        skip_friday = settings.skip_friday if settings else True
        skip_saturday = settings.skip_saturday if settings else False

        # جلب الموظفين النشطين الليهم بصمة
        users = User.query.filter(User.role.in_(['sales', 'worker']), User.has_flexible_hours == False).all()
        
        records_added = 0
        
        # لا تقم بإنشاء سجلات غياب لليوم الحالي (today) لتجنب منع الموظفين من تسجيل الحضور
        # التوليد التلقائي للغياب يكون حتى "أمس" فقط
        max_end_date = min(end_date, date.today() - timedelta(days=1))
        current_date = start_date
        
        # حلقة على كل الأيام في الفترة (حتى أمس بحد أقصى)
        while current_date <= max_end_date:
            # التحقق هل اليوم يقع خارج أيام العمل الرسمية حسب الإعدادات
            is_friday = current_date.weekday() == 4
            is_saturday = current_date.weekday() == 5
            
            if (is_friday and skip_friday) or (is_saturday and skip_saturday):
                current_date += timedelta(days=1)
                continue
                
            # جلب كل سجلات الحضور الموجودة بالفعل لهذا اليوم
            existing_attendances = Attendance.query.filter_by(date=current_date).all()
            attended_user_ids = {att.user_id for att in existing_attendances}
            
            # معرفة الموظفين الذين ليس لديهم سجل حضور في هذا اليوم
            for user in users:
                if user.id not in attended_user_ids:
                    # التحقق من وجود إذن كامل لهذا اليوم، يعتبر كأنه سجل أو يترك الغياب للتسوية في المرتب
                    # سنقوم بإنشاء سجل 'absent' والخصم سيتم تداركه في المرتبات (بالخصم أو بمسامحة الإذن)
                    new_att = Attendance(
                        user_id=user.id,
                        date=current_date,
                        status='absent'
                    )
                    db.session.add(new_att)
                    records_added += 1
            
            current_date += timedelta(days=1)
            
        if records_added > 0:
            db.session.commit()
            
    except Exception as e:
        print(f"Error filling missing attendances: {e}")
        db.session.rollback()



@app.route('/api/attendance', methods=['POST'])
@login_required
def attendance():
    data = request.get_json()
    lat = data.get('lat')
    lng = data.get('lng')
    action = data.get('action')
    if not lat or not lng: return jsonify({'error': 'لم يتم تحديد الموقع'}), 400
    distance = calculate_distance(lat, lng, FACTORY_LAT, FACTORY_LNG)
    if distance > ALLOWED_RADIUS: return jsonify({'error': f'أنت بعيد عن المصنع ({int(distance)}م). المسموح 30م.'}), 403
    today = date.today()
    record = Attendance.query.filter_by(user_id=current_user.id, date=today).first()
    if action == 'check_in':
        if record: return jsonify({'error': 'تم تسجيل الحضور مسبقاً'}), 400
        status = 'present'
        if current_user.shift_start:
            try:
                shift_time = datetime.strptime(current_user.shift_start, '%H:%M').time()
                grace_limit = datetime.combine(today, shift_time) + timedelta(minutes=15)
                if cairo_now() > grace_limit: status = 'late'
            except: pass
        db.session.add(Attendance(user_id=current_user.id, check_in=cairo_now(), status=status))
        db.session.commit()
        return jsonify({'success': f"تم تسجيل الحضور ({'متأخر ⚠️' if status=='late' else '✅'})"})
    elif action == 'check_out':
        if not record:
            # ربما الموظف يعمل في وردية تمتد لبعد منتصف الليل ويريد تسجيل الانصراف الآن بناءً على حضور الأمس
            yesterday = today - timedelta(days=1)
            record_yesterday = Attendance.query.filter_by(user_id=current_user.id, date=yesterday).first()
            if record_yesterday and not record_yesterday.check_out:
                record = record_yesterday
            else:
                return jsonify({'error': 'لم تسجل حضور اليوم أو أمس!'}), 400
                
        record.check_out = cairo_now(); db.session.commit()
        return jsonify({'success': 'تم تسجيل الانصراف بنجاح 👋'})
    return jsonify({'error': 'Invalid Action'}), 400


@app.route('/api/attendance/edit', methods=['POST'])
@login_required
def edit_attendance():
    if current_user.role != 'general_manager':
        return jsonify({'error': 'غير مسموح'}), 403
    data = request.get_json()
    rec_id = data.get('id')
    record = Attendance.query.get(rec_id)
    if not record:
        return jsonify({'error': 'سجل غير موجود'}), 404

    new_check_in = data.get('check_in')
    new_check_out = data.get('check_out')
    new_status = data.get('status')

    if new_check_in:
        try:
            record.check_in = datetime.strptime(new_check_in, '%Y-%m-%dT%H:%M')
        except:
            pass
    if new_check_out:
        try:
            record.check_out = datetime.strptime(new_check_out, '%Y-%m-%dT%H:%M')
        except:
            pass
    elif new_check_out == '':
        record.check_out = None

    if new_status:
        record.status = new_status

    db.session.commit()
    return jsonify({'success': 'تم تعديل سجل الحضور بنجاح ✅'})


@app.route('/hr/attendance_settings', methods=['GET', 'POST'])
@login_required
def attendance_settings():
    if current_user.role != 'general_manager':
        flash('غير مسموح', 'danger')
        return redirect('/')
    settings = AttendanceSettings.query.first()
    if not settings:
        settings = AttendanceSettings()
        db.session.add(settings)
        db.session.commit()

    if request.method == 'POST':
        settings.grace_period = int(request.form.get('grace_period', 15))
        settings.tier1_max_mins = int(request.form.get('tier1_max_mins', 30))
        settings.tier1_penalty = float(request.form.get('tier1_penalty', 0.25))
        settings.tier2_max_mins = int(request.form.get('tier2_max_mins', 60))
        settings.tier2_penalty = float(request.form.get('tier2_penalty', 0.5))
        settings.tier3_max_mins = int(request.form.get('tier3_max_mins', 120))
        settings.tier3_penalty = float(request.form.get('tier3_penalty', 1.0))
        settings.tier4_penalty = float(request.form.get('tier4_penalty', 2.0))
        settings.absent_no_excuse = float(request.form.get('absent_no_excuse', 1.0))
        settings.absent_excused = float(request.form.get('absent_excused', 0.5))
        settings.absent_full_day_excuse = float(request.form.get('absent_full_day_excuse', 0.0))
        settings.no_checkout_penalty = float(request.form.get('no_checkout_penalty', 2.0))
        settings.skip_friday = 'skip_friday' in request.form
        settings.skip_saturday = 'skip_saturday' in request.form
        db.session.commit()
        flash('تم حفظ إعدادات الجزاءات بنجاح ✅', 'success')
        return redirect('/hr/attendance_settings')

    return render_template('attendance_settings.html', s=settings)


@app.route('/hr/add_excuse', methods=['POST'])
@login_required
def add_excuse():
    if not current_user.has_perm('manage_hr') and current_user.role != 'general_manager':
        return jsonify({'success': False, 'message': 'غير مصرح'}), 403

    try:
        user_id = request.form.get('user_id')
        excuse_date = datetime.strptime(request.form.get('date'), '%Y-%m-%d').date()
        excuse_type = request.form.get('type')
        hours = float(request.form.get('hours') or 0)
        note = request.form.get('note')

        new_excuse = EmployeeExcuse(
            user_id=user_id,
            date=excuse_date,
            type=excuse_type,
            hours=hours,
            note=note
        )
        db.session.add(new_excuse)

        # تحديث سجل الحضور إذا كان موجوداً ليكون "بإذن"
        att_record = Attendance.query.filter_by(user_id=user_id, date=excuse_date).first()
        if att_record and excuse_type == 'day':
            att_record.status = 'absent_excused' # حالة جديدة للغياب المبرر

        db.session.commit()
        return jsonify({'success': True, 'message': 'تم تسجيل الإذن بنجاح ✅'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/team/add_member', methods=['POST'])
@permission_required('manage_hr')
def add_team_member():
    if current_user.role not in ['general_manager', 'manager']:
        return "غير مصرح لك", 403

    fullname = request.form['fullname']
    username = request.form['username']
    password = request.form['password']
    role = request.form.get('role', 'sales')
    job_type = request.form['job_type']
    phone = request.form['phone']

    shift_start = request.form.get('shift_start', '09:00')
    shift_end = request.form.get('shift_end', '17:00')

    raw_s = request.form.get('base_salary', '')
    base_salary = float(raw_s) if raw_s and raw_s.strip() else 0.0

    raw_c = request.form.get('commission_value', '')
    comm_val = float(raw_c) if raw_c and raw_c.strip() else 0.0

    tiers = []
    if job_type == 'tiered_sales':
        for i in range(1, 5):
            s = request.form.get(f'tier_{i}_start')
            e = request.form.get(f'tier_{i}_end')
            a = request.form.get(f'tier_{i}_amount')
            if s and s.strip() and e and e.strip() and a and a.strip():
                tiers.append({'min': float(s), 'max': float(e), 'val': float(a)})

    emp_code = f"EMP{''.join(random.choices(string.digits, k=3))}"

    manager_id = request.form.get('manager_id')
    if manager_id and manager_id.isdigit():
        manager_id = int(manager_id)
    else:
        manager_id = current_user.id if current_user.role in ['manager', 'general_manager'] else None

    # --- التصحيح هنا: تغيير password_hash إلى password ---
    new_user = User(
        fullname=fullname,
        username=username,
        password=generate_password_hash(password), # <--- تم التصحيح هنا
        role=role,
        emp_code=emp_code,
        phone=phone,
        job_type=job_type,
        base_salary=base_salary,
        commission_value=comm_val,
        commission_rules=json.dumps(tiers) if tiers else None,
        manager_id=manager_id,
        shift_start=shift_start,
        shift_end=shift_end,
        is_shared_salary=request.form.get('is_shared_salary') == 'on',
        has_flexible_hours=request.form.get('has_flexible_hours') == 'on'
    )

    db.session.add(new_user)
    db.session.commit()

    flash('تم إضافة الموظف بنجاح ✅', 'success')
    return redirect(url_for('dashboard'))

@app.route('/employee/update_data/<int:id>', methods=['POST'])
@permission_required('manage_hr')
def update_employee_data(id):
    emp = User.query.get_or_404(id)

    # 1. استقبال البيانات الجديدة
    new_username = request.form.get('username')

    # 2. التحقق من اسم المستخدم (هام جداً لمنع التكرار)
    if new_username and new_username != emp.username:
        # لو غير الاسم، نتأكد إنه مش محجوز لحد تاني
        existing_user = User.query.filter_by(username=new_username).first()
        if existing_user:
            flash(f'❌ خطأ: اسم المستخدم "{new_username}" مسجل بالفعل لموظف آخر!', 'danger')
            return redirect(url_for('employee_profile', id=id))

        # لو تمام، نحدثه
        emp.username = new_username

    # 3. تحديث باقي البيانات
    emp.fullname = request.form['fullname']
    emp.phone = request.form['phone']
    emp.role = request.form['role']
    emp.emp_code = request.form['emp_code']
    emp.base_salary = float(request.form['base_salary'])
    emp.job_type = request.form['job_type']

    if 'commission_value' in request.form:
        emp.commission_value = float(request.form['commission_value'])

    emp.shift_start = request.form.get('shift_start')
    emp.shift_end = request.form.get('shift_end')

    manager_id = request.form.get('manager_id')
    if manager_id and manager_id.isdigit():
        emp.manager_id = int(manager_id)

    # تحديث الشرائح (لو موجودة)
    if emp.job_type == 'tiered_sales':
        tiers = []
        for i in range(1, 5):
            s, e, a = request.form.get(f'tier_{i}_start'), request.form.get(f'tier_{i}_end'), request.form.get(f'tier_{i}_amount')
            if s and s.strip():
                tiers.append({'min': float(s), 'max': float(e), 'val': float(a)})
        emp.commission_rules = json.dumps(tiers) if tiers else None
    else:
        emp.commission_rules = None

    emp.is_shared_salary = request.form.get('is_shared_salary') == 'on'
    emp.has_flexible_hours = request.form.get('has_flexible_hours') == 'on'

    db.session.commit()
    flash('تم تحديث بيانات الموظف (بما فيها اسم الدخول) بنجاح ✅', 'success')
    return redirect(url_for('employee_profile', id=emp.id))

@app.route('/employee/delete/<int:id>')
@general_manager_required
def delete_employee(id):
    user = User.query.get_or_404(id)
    if user.id == current_user.id: flash('لا يمكن حذف حسابك', 'danger'); return redirect(request.referrer)
    try:
        Attendance.query.filter_by(user_id=id).delete()
        db.session.delete(user); db.session.commit()
        flash('تم الحذف', 'success')
    except: db.session.rollback(); flash('خطأ أثناء الحذف', 'danger')
    return redirect(url_for('dashboard'))


@app.route('/employee/<int:id>', methods=['GET', 'POST'])
@login_required
def employee_profile(id):
    emp = User.query.get_or_404(id)

    # === معالجة إضافة (مكافأة / خصم / سلفة) ===
    if request.method == 'POST':
        if not current_user.has_perm('manage_hr') and current_user.role != 'general_manager':
            return "غير مصرح", 403

        try:
            amount = float(request.form['amount'])
            t_type = request.form['type'] # bonus, deduction, advance
            note = request.form.get('note', '')
            account_id = request.form.get('account_id') # استقبال رقم الخزنة

            # 1. تسجيل الحركة في ملف الموظف
            db.session.add(HRTransaction(
                user_id=emp.id,
                type=t_type,
                amount=amount,
                note=note,
                date=cairo_now()
            ))

            # 2. التأثير على المدير المباشر
            payer_partner = None
            if emp.role == 'manager':
                payer_partner = emp
            elif emp.manager_id:
                payer_partner = User.query.get(emp.manager_id)

            if payer_partner:
                if t_type == 'advance':
                    if payer_partner and emp.id != payer_partner.id:
                        # === تقسيم السلفة حسب نوع الموظف ===
                        if emp.is_shared_salary:
                            # موظف مشترك: 50% على المدير العام + 50% على 4 شركاء
                            gm = User.query.filter_by(role='general_manager').first()
                            managers = User.query.filter_by(role='manager').all()
                            if gm:
                                db.session.add(PartnerTransaction(
                                    partner_id=gm.id, type='staff_expense',
                                    amount=-(amount / 2),
                                    description=f"سلفة موظف مشترك 50% ({emp.fullname}): {note}"
                                ))
                            if managers:
                                share = (amount / 2) / len(managers)
                                for m in managers:
                                    db.session.add(PartnerTransaction(
                                        partner_id=m.id, type='staff_expense',
                                        amount=-share,
                                        description=f"سلفة موظف مشترك - حصة شريك ({emp.fullname}): {note}"
                                    ))
                        else:
                            db.session.add(PartnerTransaction(
                                partner_id=payer_partner.id,
                                type='staff_expense',
                                amount=-amount,
                                description=f"سلفة للموظف ({emp.fullname}): {note}"
                            ))
                    elif emp.id == payer_partner.id: # لو المدير نفسه هو اللي ساحب
                        db.session.add(PartnerTransaction(
                            partner_id=payer_partner.id,
                            type='withdrawal',
                            amount=-amount,
                            description=f"سحب شخصي: {note}"
                        ))

                    # 2. خصم المبلغ من الخزينة المحددة
                    if account_id:
                        account = MoneyAccount.query.get(account_id)
                        if account:
                            account.balance -= amount
                            db.session.add(FinancialTransaction(
                                account_id=account.id,
                                type='expense',
                                category='سلف موظفين',
                                amount=-amount,
                                description=f"صرف سلفة نقدية لـ {emp.fullname}",
                                created_by_id=current_user.id,
                                date=cairo_now()
                            ))
                    else:
                        cash_acc = MoneyAccount.query.filter_by(type='cash').first()
                        if cash_acc:
                            cash_acc.balance -= amount
                            db.session.add(FinancialTransaction(
                                account_id=cash_acc.id,
                                type='expense',
                                category='سلف موظفين',
                                amount=-amount,
                                description=f"سلفة نقدية لـ {emp.fullname}",
                                created_by_id=current_user.id,
                                date=cairo_now()
                            ))

                elif t_type == 'bonus':
                    if emp.id != payer_partner.id:
                        # مكافأة لموظف (سيلز أو عامل) -> تحول إلى admin_bonus بالسالب
                        if emp.is_shared_salary:
                            gm = User.query.filter_by(role='general_manager').first()
                            managers = User.query.filter_by(role='manager').all()
                            if gm:
                                db.session.add(PartnerTransaction(partner_id=gm.id, type='admin_bonus', amount=-(amount / 2), description=f"مكافأة موظف مشترك 50% ({emp.fullname}): {note}"))
                            if managers:
                                share = (amount / 2) / len(managers)
                                for m in managers:
                                    db.session.add(PartnerTransaction(partner_id=m.id, type='admin_bonus', amount=-share, description=f"مكافأة موظف مشترك - حصة شريك ({emp.fullname}): {note}"))
                        else:
                            db.session.add(PartnerTransaction(partner_id=payer_partner.id, type='admin_bonus', amount=-amount, description=f"مكافأة للموظف ({emp.fullname}): {note}"))
                    else:
                        # مكافأة للمدير نفسه (Partner Bonus)
                        db.session.add(PartnerTransaction(
                            partner_id=emp.id, type='admin_bonus', amount=amount, 
                            description=f"مكافأة إدارية من المدير العام: {note}", date=cairo_now()
                        ))

                elif t_type == 'deduction':
                    if amount > 0:
                        if emp.id != payer_partner.id:
                            # خصم من موظف -> يحول إلى admin_penalty بالموجب
                            if emp.is_shared_salary:
                                gm = User.query.filter_by(role='general_manager').first()
                                managers = User.query.filter_by(role='manager').all()
                                if gm:
                                    db.session.add(PartnerTransaction(partner_id=gm.id, type='admin_penalty', amount=(amount / 2), description=f"خصم موظف مشترك 50% ({emp.fullname}): {note}"))
                                if managers:
                                    share = (amount / 2) / len(managers)
                                    for m in managers:
                                        db.session.add(PartnerTransaction(partner_id=m.id, type='admin_penalty', amount=share, description=f"خصم موظف مشترك - حصة شريك ({emp.fullname}): {note}"))
                            else:
                                db.session.add(PartnerTransaction(partner_id=payer_partner.id, type='admin_penalty', amount=amount, description=f"خصم/جزاء على ({emp.fullname}): {note}"))
                        else:
                            # خصم من المدير نفسه (Partner Penalty)
                            db.session.add(PartnerTransaction(
                                partner_id=emp.id, type='admin_penalty', amount=-amount, 
                                description=f"جزاء إداري من المدير العام: {note}", date=cairo_now()
                            ))

            db.session.commit()
            flash('تم تسجيل الحركة المالية بنجاح ✅', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'خطأ: {e}', 'warning')

        return redirect(url_for('employee_profile', id=id))

    # === الحسابات المالية وعرض البيانات ===
    today = date.today()
    default_month = today.strftime('%Y-%m')
    
    start_month_str = request.args.get('start_month', default_month)
    end_month_str = request.args.get('end_month', default_month)

    try:
        month_start = datetime.strptime(start_month_str, '%Y-%m')
    except ValueError:
        month_start = cairo_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        start_month_str = month_start.strftime('%Y-%m')

    try:
        end_dt = datetime.strptime(end_month_str, '%Y-%m')
    except ValueError:
        end_dt = month_start
        end_month_str = start_month_str
        
    # Ensure start is before end
    if month_start > end_dt:
        month_start, end_dt = end_dt, month_start
        start_month_str, end_month_str = end_month_str, start_month_str

    if end_dt.month == 12:
        month_end = end_dt.replace(year=end_dt.year + 1, month=1)
    else:
        month_end = end_dt.replace(month=end_dt.month + 1)
        
    # حساب المرتجعات في الفترة (حسب تاريخ المرتجع، حتى لو البيع من شهر قديم)
    returned_items_all = db.session.query(func.sum(ReturnInvoice.total_qty))\
        .join(SaleOrder)\
        .filter(SaleOrder.user_id == emp.id,
                ReturnInvoice.date >= month_start,
                ReturnInvoice.date < month_end).scalar() or 0

    # مرتجعات الفواتير التي تمت في نفس الشهر فقط
    returned_items_same_month = db.session.query(func.sum(ReturnInvoice.total_qty))\
        .join(SaleOrder)\
        .filter(SaleOrder.user_id == emp.id,
                ReturnInvoice.date >= month_start,
                ReturnInvoice.date < month_end,
                SaleOrder.date >= month_start,
                SaleOrder.date < month_end).scalar() or 0

    # حساب المبيعات والقطع قبل الخصومات والمرتجعات
    monthly_sales = db.session.query(func.sum(SaleOrder.final_total)).filter(SaleOrder.user_id == emp.id, SaleOrder.is_proforma == False, SaleOrder.date >= month_start, SaleOrder.date < month_end).scalar() or 0
    orders_count = SaleOrder.query.filter(SaleOrder.user_id == emp.id, SaleOrder.is_proforma == False, SaleOrder.date >= month_start, SaleOrder.date < month_end).count()
    gross_items = db.session.query(func.sum(SaleItem.quantity)).join(SaleOrder).filter(SaleOrder.user_id == emp.id, SaleOrder.is_proforma == False, SaleOrder.date >= month_start, SaleOrder.date < month_end).scalar() or 0
    company_gross_items = db.session.query(func.sum(SaleItem.quantity)).join(SaleOrder).filter(SaleOrder.is_proforma == False, SaleOrder.date >= month_start, SaleOrder.date < month_end).scalar() or 0

    net_for_tier = gross_items - returned_items_all
    if net_for_tier < 0: net_for_tier = 0

    net_for_payment = gross_items - returned_items_same_month
    if net_for_payment < 0: net_for_payment = 0
    
    commission = calculate_user_commission(emp, net_for_payment, net_for_tier)
    num_months = (end_dt.year - month_start.year) * 12 + end_dt.month - month_start.month + 1
    selected_period = f"{start_month_str}" if start_month_str == end_month_str else f"{start_month_str} إلى {end_month_str}"
    period_base_salary = (emp.base_salary or 0) * num_months

    hr_trans = HRTransaction.query.filter(HRTransaction.user_id == emp.id, HRTransaction.date >= month_start, HRTransaction.date < month_end).all()

    bonuses = 0; real_deductions = 0; advances = 0
    for t in hr_trans:
        if t.type == 'bonus': bonuses += t.amount
        elif t.type == 'advance': advances += t.amount
        elif t.type in ('deduction', 'penalty'):
            real_deductions += t.amount

    # حساب المرتجعات في الفترة (حسب تاريخ المرتجع، حتى لو البيع من شهر قديم)
    returned_items = db.session.query(func.sum(ReturnInvoice.total_qty))\
        .join(SaleOrder)\
        .filter(SaleOrder.user_id == emp.id,
                ReturnInvoice.date >= month_start,
                ReturnInvoice.date < month_end).scalar() or 0

    net_items = gross_items - returned_items
    if net_items < 0: net_items = 0
    
    # Company net items (all employees)
    company_returned_items_total = db.session.query(func.sum(ReturnInvoice.total_qty))\
        .filter(ReturnInvoice.date >= month_start,
                ReturnInvoice.date < month_end).scalar() or 0
    company_net_items = company_gross_items - company_returned_items_total
    if company_net_items < 0: company_net_items = 0
    
    sales_percentage = (net_items / company_net_items * 100) if company_net_items > 0 else 0
    
    # 5. حساب جزاءات الحضور
    att_settings = AttendanceSettings.query.first()
    if not att_settings:
        att_settings = AttendanceSettings()
    daily_rate = (emp.base_salary or 0) / 30
    attendance_deduction = 0
    
    att_stats = {'present': 0, 'absent': 0, 'late_mins': 0}

    if not emp.has_flexible_hours:
        attendance_records = Attendance.query.filter(
            Attendance.user_id == emp.id,
            Attendance.date >= month_start,
            Attendance.date < month_end
        ).all()
        for rec in attendance_records:
            if att_settings.skip_friday and rec.date.weekday() == 4: continue
            if att_settings.skip_saturday and rec.date.weekday() == 5: continue
            excuse = EmployeeExcuse.query.filter_by(user_id=emp.id, date=rec.date).first()
            day_deduction = 0
            if rec.status == 'absent':
                att_stats['absent'] += 1
                if excuse and excuse.type == 'day':
                    day_deduction = daily_rate * att_settings.absent_full_day_excuse
                elif getattr(rec, 'is_excused', False):
                    day_deduction = daily_rate * att_settings.absent_excused
                else:
                    day_deduction = daily_rate * att_settings.absent_no_excuse
            else:
                att_stats['present'] += 1
                late_mins = 0
                if rec.check_in and emp.shift_start:
                    try:
                        shift_t = datetime.strptime(emp.shift_start, '%H:%M').time()
                        check_in_t = rec.check_in.time()
                        shift_minutes = shift_t.hour * 60 + shift_t.minute
                        checkin_minutes = check_in_t.hour * 60 + check_in_t.minute
                        late_mins = max(0, checkin_minutes - shift_minutes)
                    except: pass
                early_mins = 0
                if rec.check_out and emp.shift_end:
                    try:
                        shift_end_t = datetime.strptime(emp.shift_end, '%H:%M').time()
                        check_out_t = rec.check_out.time()
                        end_minutes = shift_end_t.hour * 60 + shift_end_t.minute
                        checkout_minutes = check_out_t.hour * 60 + check_out_t.minute
                        if emp.shift_start:
                            shift_start_t = datetime.strptime(emp.shift_start, '%H:%M').time()
                            shift_minutes_chk = shift_start_t.hour * 60 + shift_start_t.minute
                            if end_minutes <= shift_minutes_chk:
                                end_minutes += 1440
                                if checkout_minutes < shift_minutes_chk:
                                    checkout_minutes += 1440
                        early_mins = max(0, end_minutes - checkout_minutes)
                    except: pass
                elif not rec.check_out:
                    if not emp.has_flexible_hours:
                        day_deduction = daily_rate * att_settings.no_checkout_penalty
                    attendance_deduction += day_deduction
                    continue
                total_lost_mins = late_mins + early_mins
                if excuse and excuse.type == 'hours':
                    total_lost_mins = max(0, total_lost_mins - (excuse.hours * 60))
                
                if total_lost_mins > 0:
                    att_stats['late_mins'] += total_lost_mins
                    
                if total_lost_mins > att_settings.grace_period:
                    if total_lost_mins <= att_settings.tier1_max_mins:
                        day_deduction = daily_rate * att_settings.tier1_penalty
                    elif total_lost_mins <= att_settings.tier2_max_mins:
                        day_deduction = daily_rate * att_settings.tier2_penalty
                    elif total_lost_mins <= att_settings.tier3_max_mins:
                        day_deduction = daily_rate * att_settings.tier3_penalty
                    else:
                        day_deduction = daily_rate * att_settings.tier4_penalty
            attendance_deduction += day_deduction

    net_salary = period_base_salary + commission + bonuses - real_deductions - advances - attendance_deduction

    current_tiers = []
    if emp.commission_rules:
        try: current_tiers = json.loads(emp.commission_rules)
        except: pass
    while len(current_tiers) < 4: current_tiers.append({'min': '', 'max': '', 'val': ''})

    recent_orders = SaleOrder.query.filter(SaleOrder.user_id == emp.id, SaleOrder.is_proforma == False, SaleOrder.date >= month_start, SaleOrder.date < month_end).order_by(SaleOrder.date.desc()).all()
    
    returned_orders_count = 0
    for order in recent_orders:
        order_qty = sum(item.quantity for item in order.items)
        ret_details = order.return_details
        returned_qty = ret_details['returned_qty'] if ret_details else 0
        if returned_qty > 0:
            returned_orders_count += 1
        returned_qty = ret_details['returned_qty'] if ret_details else 0
        net_order_qty = order_qty - returned_qty
        if net_order_qty < 0: net_order_qty = 0
        
        order.returned_qty_cached = returned_qty
        order.net_qty_cached = net_order_qty
        order.calculated_commission = calculate_user_commission(emp, net_order_qty, net_for_tier)

    transactions = HRTransaction.query.filter(HRTransaction.user_id == emp.id, HRTransaction.date >= month_start, HRTransaction.date < month_end).order_by(HRTransaction.date.desc()).all()

    # جلب الحسابات لإرسالها للقالب
    accounts = MoneyAccount.query.all()
    all_managers = User.query.filter(User.role.in_(['manager', 'general_manager'])).all()

    return render_template('employee_profile.html',
                           emp=emp,
                           sales=monthly_sales,
                           orders_count=orders_count,
                           total_items=int(net_items),
                           gross_items=int(gross_items),
                           sales_percentage=sales_percentage,
                           returns_percentage=(returned_orders_count / orders_count * 100) if orders_count > 0 else 0,
                           returned_items=int(returned_items),
                           commission=round(commission, 2),
                           bonuses=bonuses,
                           deductions=real_deductions,
                           advances=advances,
                           attendance_deduction=round(attendance_deduction, 2),
                           att_stats=att_stats,
                           att_records=attendance_records if not emp.has_flexible_hours else [],
                           net_salary=round(net_salary, 2),
                           period_base_salary=period_base_salary,
                           transactions=transactions,
                           orders=recent_orders,
                           current_tiers=current_tiers,
                           all_managers=all_managers,
                           accounts=accounts,
                           selected_period=selected_period,
                           start_month=start_month_str,
                           end_month=end_month_str)

@app.route('/api/pay_salary', methods=['POST'])
@login_required
def pay_salary():
    # 1. التحقق من الصلاحية (HR أو المدير العام)
    if not current_user.has_perm('manage_hr') and current_user.role != 'general_manager':
        return jsonify({'success': False, 'message': 'غير مصرح'}), 403

    try:
        # 2. استقبال البيانات من الطلب (بما فيها العمولة والشهر)
        user_id = request.form.get('user_id')
        net_payout = float(request.form.get('amount')) # المبلغ الصافي اللي الموظف هياخده في إيده
        commission_val = float(request.form.get('commission_amount', 0)) # قيمة العمولة فقط
        month_context = request.form.get('month', '') # مثال: 2026-01
        account_id = request.form.get('account_id') # رقم الخزينة

        # 3. التحقق من الموظف والخزينة
        employee = User.query.get(user_id)
        if not employee:
            return jsonify({'success': False, 'message': 'الموظف غير موجود'}), 404

        account = MoneyAccount.query.get(account_id)
        if not account:
            return jsonify({'success': False, 'message': 'يجب اختيار خزينة صحيحة'}), 400

        # 4. معالجة الخزينة: خصم المبلغ الصافي وتسجيل الحركة المالية
        account.balance = round(account.balance - net_payout, 1)
        db.session.add(FinancialTransaction(
            account_id=account.id,
            type='expense',
            category='رواتب',
            amount=-net_payout,
            description=f"صرف راتب شهر {month_context} للموظف {employee.fullname}",
            created_by_id=current_user.id,
            date=cairo_now()
        ))

        # 5. تسجيل الحركة في ملف الموظف (لإخفاء زر الصرف لاحقاً)
        db.session.add(HRTransaction(
            user_id=user_id,
            type='salary_payment',
            amount=net_payout,
            note=f"صرف راتب شهر {month_context}" if month_context else "صرف راتب شهري",
            date=cairo_now()
        ))

        # 6. توزيع الخصم المالي على حسابات الشركاء (المعادلة المحاسبية الدقيقة)

        # الجزء الذي سيتحمله الشركاء = (الصافي المستلم - العمولة)
        # طرحنا العمولة لأن أحمد سيتحملها منفرداً 100%
        base_salary_part = net_payout - commission_val

        gm = User.query.filter_by(role='general_manager').first() # أ/أحمد عبد الفتاح
        partners = User.query.filter_by(role='manager').all() # الشركاء الأربعة

        # ب) توزيع الجزء الخاص بالراتب (الأساسي وما يتبعه)
        # ملاحظة: تم إزالة خصم العمولة من المدير العام منعاً للازدواجية، لأن الشريك المباشر هو من يتحملها من أرباحه.
        if base_salary_part > 0:
            if employee.is_shared_salary:
                # موظف مشترك: أحمد يشيل 50% والشركاء الأربعة يشيلوا 50%
                if gm:
                    db.session.add(PartnerTransaction(
                        partner_id=gm.id, type='staff_expense',
                        amount=-(base_salary_part / 2),
                        description=f"حصة 50% من راتب موظف مشترك ({month_context}): {employee.fullname}"
                    ))
                if partners:
                    share = (base_salary_part / 2) / len(partners)
                    for p in partners:
                        db.session.add(PartnerTransaction(
                            partner_id=p.id, type='staff_expense',
                            amount=-share,
                            description=f"حصة شريك من راتب موظف مشترك ({month_context}): {employee.fullname}"
                        ))
            else:
                # موظف عادي: مديره المباشر يتحمل باقي الصافي بالكامل
                if employee.manager_id:
                    manager = User.query.get(employee.manager_id)
                    if manager and manager.role in ['manager', 'general_manager']:
                        db.session.add(PartnerTransaction(
                            partner_id=manager.id,
                            type='staff_expense',
                            amount=-base_salary_part,
                            description=f"تحمل صافي راتب شهر {month_context} (بدون عمولة): {employee.fullname}"
                        ))

        # 7. تسجيل المصروف في سجل الشركة العام (Reference Only)
        # ملاحظة: is_shared=False لأن المبلغ خُصم بالفعل من الشركاء في الخطوة السابقة
        sal_cat = ExpenseCategory.query.filter_by(name="رواتب").first()
        if not sal_cat:
            sal_cat = ExpenseCategory(name="رواتب")
            db.session.add(sal_cat)
            db.session.flush()

        db.session.add(Expense(
            category_id=sal_cat.id,
            amount=net_payout,
            description=f"صرف راتب: {employee.fullname} ({month_context})",
            date=cairo_now(),
            user_id=current_user.id,
            is_shared=False,
            account_id=account.id
        ))

        # 8. حفظ كافة التغييرات
        db.session.commit()
        return jsonify({'success': True, 'message': 'تم صرف الراتب وتوزيعه محاسبياً بدقة ✅'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'حدث خطأ غير متوقع: {str(e)}'}), 500


@app.route('/hr/attendance_report')
@login_required
def attendance_report():
    # التحقق من الصلاحية
    if not current_user.has_perm('manage_hr') and current_user.role != 'general_manager':
        flash('غير مصرح لك', 'danger')
        return redirect(url_for('dashboard'))

    # الفلاتر (الشهر والموظف)
    month_str = request.args.get('month', date.today().strftime('%Y-%m'))
    
    # [تحديث] ملأ أيام الغياب اللي الموظف مبصمش فيها خالص
    fill_missing_attendances(month_str)

    user_id = request.args.get('user_id')

    query = Attendance.query.filter(func.to_char(Attendance.date, 'YYYY-MM') == month_str)

    selected_user = None
    if user_id and user_id != 'all':
        query = query.filter(Attendance.user_id == user_id)
        selected_user = User.query.get(user_id)

    records = query.order_by(Attendance.date.desc(), Attendance.check_in.asc()).all()

    # حساب الإحصائيات للفترة المحددة
    stats = {
        'total_days': len(records),
        'late_days': sum(1 for r in records if r.status == 'late'),
        'total_hours': 0
    }

    # تجهيز البيانات للعرض
    attendance_data = []
    for r in records:
        work_hours = "---"
        if r.check_in and r.check_out:
            diff = r.check_out - r.check_in
            seconds = diff.total_seconds()
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            work_hours = f"{hours}س {minutes}د"
            stats['total_hours'] += hours # جمع تقريبي للساعات

        attendance_data.append({
            'user': r.user.fullname,
            'date': r.date,
            'check_in': r.check_in,
            'check_out': r.check_out,
            'status': r.status,
            'work_hours': work_hours
        })

    users = User.query.all()
    return render_template('attendance_report.html',
                         records=attendance_data,
                         users=users,
                         selected_month=month_str,
                         selected_user=int(user_id) if user_id and user_id != 'all' else None,
                         stats=stats)

@app.route('/hr/payroll')
@login_required
def payroll():
    # التحقق من الصلاحيات
    if not current_user.has_perm('manage_hr') and current_user.role != 'general_manager':
        return "غير مصرح", 403

    # استلام الشهر من الرابط أو افتراض الشهر الحالي
    month_str = request.args.get('month', date.today().strftime('%Y-%m'))
    
    # [تحديث] ملأ أيام الغياب اللي الموظف مبصمش فيها خالص
    fill_missing_attendances(month_str)

    # جلب جميع الخزائن المتاحة للصرف
    accounts = MoneyAccount.query.all()
    employees_data = []

    # جلب الموظفين (سيلز وعمال) فقط
    users = User.query.filter(User.role.in_(['sales', 'worker'])).all()

    # تحميل إعدادات الجزاءات
    att_settings = AttendanceSettings.query.first()
    if not att_settings:
        att_settings = AttendanceSettings()
        db.session.add(att_settings)
        db.session.commit()

    for u in users:
        # حساب أجر اليوم الواحد
        daily_rate = (u.base_salary or 0) / 30

        # 1. حساب "تراكمي الموسم" (من 1 يناير 2025) لتحديد شريحة العمولة
        total_season_items = db.session.query(func.sum(SaleItem.quantity))\
            .join(SaleOrder)\
            .filter(SaleOrder.user_id == u.id,
                    SaleOrder.is_proforma == False,
                    SaleOrder.date >= SEASON_START,
                    SaleOrder.date <= SEASON_END).scalar() or 0

        # 2. حساب قطع "الشهر الحالي" فقط لحساب مبلغ العمولة المستحق الآن
        current_month_items = db.session.query(func.sum(SaleItem.quantity))\
            .join(SaleOrder)\
            .filter(SaleOrder.user_id == u.id,
                    SaleOrder.is_proforma == False,
                    func.to_char(SaleOrder.date, 'YYYY-MM') == month_str).scalar() or 0

        # === [تعديل] خصم المرتجعات من عدد القطع ===
        # أ) مرتجعات الموسم ككل (عشان الشريحة تكون صح)
        returned_items_season = db.session.query(func.sum(ReturnInvoice.total_qty))\
            .join(SaleOrder)\
            .filter(SaleOrder.user_id == u.id,
                    ReturnInvoice.date >= SEASON_START,
                    ReturnInvoice.date <= SEASON_END).scalar() or 0
        
        net_season_items = max(0, total_season_items - returned_items_season)

        # ب) مرتجعات الشهر الحالي (حسب تاريخ المرتجع، حتى لو البيع من شهر قديم)
        returned_items_current_month = db.session.query(func.sum(ReturnInvoice.total_qty))\
            .join(SaleOrder)\
            .filter(SaleOrder.user_id == u.id,
                    func.to_char(ReturnInvoice.date, 'YYYY-MM') == month_str).scalar() or 0

        # ج) مرتجعات نفس الشهر (البيع والمرتجع في نفس الشهر)
        same_month_returns_qty = db.session.query(func.sum(ReturnInvoice.total_qty))\
            .join(SaleOrder)\
            .filter(SaleOrder.user_id == u.id,
                    func.to_char(ReturnInvoice.date, 'YYYY-MM') == month_str,
                    func.to_char(SaleOrder.date, 'YYYY-MM') == month_str).scalar() or 0

        # د) مرتجعات من أشهر سابقة (المرتجع في الشهر الحالي والبيع من شهر قديم)
        cross_month_returns_qty = max(0, returned_items_current_month - same_month_returns_qty)

        # هـ) تحديد فئة العمولة (الشريحة): المبيعات - كل المرتجعات اللي حصلت الشهر ده
        net_for_tier = max(0, current_month_items - returned_items_current_month)

        # و) مبلغ العمولة: المبيعات - مرتجعات نفس الشهر فقط
        net_for_payment = max(0, current_month_items - same_month_returns_qty)

        # ز) حساب العمولة بناءً على الشريحة
        gross_commission = calculate_user_commission(u, net_for_payment, net_for_tier)

        # 3. حساب جزاءات الحضور (تأخير + انصراف مبكر + غياب)
        if u.has_flexible_hours:
            attendance_records = []
        else:
            attendance_records = Attendance.query.filter(Attendance.user_id == u.id,
                                                       func.to_char(Attendance.date, 'YYYY-MM') == month_str).all()

        attendance_deduction = 0
        attendance_details = []  # تفاصيل الجزاءات يوم بيوم
        for rec in attendance_records:
            # تخطي أيام الإجازة حسب الإعدادات
            if att_settings.skip_friday and rec.date.weekday() == 4: continue
            if att_settings.skip_saturday and rec.date.weekday() == 5: continue

            # البحث عن إذن في هذا اليوم
            excuse = EmployeeExcuse.query.filter_by(user_id=u.id, date=rec.date).first()
            day_deduction = 0
            day_reason = ''

            if rec.status == 'absent':
                # جزاء الغياب حسب الإعدادات
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
                # حساب التأخير والانصراف - مقارنة الوقت فقط (بدون التاريخ)
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
                    
                    # معالجة الورديات التي تعبر منتصف الليل
                    # إذا كان وقت نهاية الوردية أقل من وقت بدايتها، فهذا يعني أنها تنتهي في اليوم التالي
                    if u.shift_start:
                        shift_start_t = datetime.strptime(u.shift_start, '%H:%M').time()
                        shift_minutes_chk = shift_start_t.hour * 60 + shift_start_t.minute
                        
                        if end_minutes <= shift_minutes_chk:
                            end_minutes += 1440 # إضافة 24 ساعة
                            # إذا كان الانصراف الفعلي قبل منتصف الليل بيسير (مثلا الساعة 23)، لا نضيف له 24 ساعة
                            # وإذا كان الانصراف بعد منتصف الليل (مثلا الساعة 1 أو 2)، نضيف له 24 ساعة لتوحيد المقارنة
                            if checkout_minutes < shift_minutes_chk:
                                checkout_minutes += 1440
                    
                    early_mins = max(0, end_minutes - checkout_minutes)
                elif not rec.check_out:
                    # عدم تسجيل انصراف
                    if u.has_flexible_hours:
                        day_deduction = 0
                        day_reason = 'لم يسجل انصراف (موظف بمواعيد مرنة - بدون خصم)'
                    else:
                        day_deduction = daily_rate * att_settings.no_checkout_penalty
                        day_reason = f'لم يسجل انصراف (خصم {att_settings.no_checkout_penalty} يوم)'
                        
                    if late_mins > 0:
                        day_reason = f'تأخير {int(late_mins)} دقيقة + ' + day_reason
                    
                    attendance_deduction += day_deduction
                    if day_deduction > 0 or u.has_flexible_hours:
                        attendance_details.append({
                            'rec_id': rec.id,
                            'date': rec.date.strftime('%Y-%m-%d'),
                            'day_name': ['الإثنين','الثلاثاء','الأربعاء','الخميس','الجمعة','السبت','الأحد'][rec.date.weekday()],
                            'reason': day_reason,
                            'deduction': round(day_deduction, 2),
                            'check_in': rec.check_in.strftime('%I:%M %p') if rec.check_in else '---',
                            'check_out': '---',
                            'shift_start': u.shift_start or '09:00',
                            'shift_end': u.shift_end or '17:00'
                        })
                    continue

                total_lost_mins = late_mins + early_mins

                # طرح ساعات الإذن من وقت التأخير
                if excuse and excuse.type == 'hours':
                    total_lost_mins = max(0, total_lost_mins - (excuse.hours * 60))

                # تطبيق شرائح الجزاءات حسب الإعدادات
                if not u.has_flexible_hours:
                    if total_lost_mins > att_settings.grace_period:
                        if total_lost_mins <= att_settings.tier1_max_mins:
                            day_deduction = daily_rate * att_settings.tier1_penalty
                        elif total_lost_mins <= att_settings.tier2_max_mins:
                            day_deduction = daily_rate * att_settings.tier2_penalty
                        elif total_lost_mins <= att_settings.tier3_max_mins:
                            day_deduction = daily_rate * att_settings.tier3_penalty
                        else:
                            day_deduction = daily_rate * att_settings.tier4_penalty

                    # بناء سبب تفصيلي
                    reasons = []
                    if late_mins > 0: reasons.append(f'تأخير {int(late_mins)} دقيقة')
                    if early_mins > 0: reasons.append(f'انصراف مبكر {int(early_mins)} دقيقة')
                    if excuse and excuse.type == 'hours': reasons.append(f'إذن {excuse.hours} ساعة')
                    day_reason = ' + '.join(reasons) if reasons else 'حضر وانصرف في موعده'
                else:
                    # موظف بمواعيد مرنة، لا يتم تطبيق أي جزاءات تأخير أو خروج مبكر أو عدم تسجيل انصراف
                    day_deduction = 0
                    day_reason = 'حضور وانصراف (موظف بمواعيد مرنة)'

            attendance_deduction += day_deduction

            # إضافة التفاصيل (حتى لو الخصم صفر - لإظهار كل الأيام)
            if day_deduction > 0:
                attendance_details.append({
                    'rec_id': rec.id,
                    'date': rec.date.strftime('%Y-%m-%d'),
                    'day_name': ['الإثنين','الثلاثاء','الأربعاء','الخميس','الجمعة','السبت','الأحد'][rec.date.weekday()],
                    'reason': day_reason,
                    'deduction': round(day_deduction, 2),
                    'check_in': rec.check_in.strftime('%I:%M %p') if rec.check_in else '---',
                    'check_out': rec.check_out.strftime('%I:%M %p') if rec.check_out else '---',
                    'shift_start': u.shift_start or '09:00',
                    'shift_end': u.shift_end or '17:00'
                })

        # 4. جلب كافة الحركات المالية اليدوية (مكافآت، سلف، جزاءات، مرتجعات) لهذا الشهر
        hr_trans = HRTransaction.query.filter(HRTransaction.user_id == u.id,
                                            func.to_char(HRTransaction.date, 'YYYY-MM') == month_str).all()

        bonuses = sum(t.amount for t in hr_trans if t.type == 'bonus')
        advances = sum(t.amount for t in hr_trans if t.type == 'advance')
        other_penalties = sum(t.amount for t in hr_trans if t.type == 'deduction' and not (t.note and ('مرتجع' in t.note or 'قطعة' in t.note)))
        
        # Calculate returns deduction (past months explicit return_reversal)
        past_returns_deduction = sum(abs(t.amount) for t in hr_trans if t.type == 'return_reversal')
        
        # Same-month returns are already excluded from net_for_tier → no extra deduction
        # Only cross-month (past months) return_reversal is shown
        total_returns_deduction = past_returns_deduction

        # بناء تفاصيل كل نوع للعرض في الـ drilldown
        def build_hr_details(trans_list, filter_fn):
            return [{'date': t.date.strftime('%Y-%m-%d'), 'amount': t.amount, 'note': t.note or '---'} for t in trans_list if filter_fn(t)]

        bonuses_details = build_hr_details(hr_trans, lambda t: t.type == 'bonus')
        advances_details = build_hr_details(hr_trans, lambda t: t.type == 'advance')
        penalties_details = build_hr_details(hr_trans, lambda t: t.type == 'deduction' and not (t.note and ('مرتجع' in t.note or 'قطعة' in t.note)))
        
        returns_details = []
        for t in hr_trans:
            if t.type == 'return_reversal':
                match = re.search(r'\((\d+)\s*قطعة\)', t.note)
                returned_qty = int(match.group(1)) if match else 0
                calculated_amount = abs(t.amount)
                piece_price = (calculated_amount / returned_qty) if returned_qty > 0 else 0
                note_with_piece_price = f"{t.note} - سعر العمولة: {round(piece_price, 2)} ج.م للقطعة"
                returns_details.append({
                    'date': t.date.strftime('%Y-%m-%d'),
                    'amount': round(calculated_amount, 2),
                    'note': note_with_piece_price
                })

        # 4.5 جلب الأذونات لهذا الشهر
        month_excuses = EmployeeExcuse.query.filter(
            EmployeeExcuse.user_id == u.id,
            func.to_char(EmployeeExcuse.date, 'YYYY-MM') == month_str
        ).order_by(EmployeeExcuse.date).all()

        excuses_count = len(month_excuses)
        excuses_details = [{
            'date': ex.date.strftime('%Y-%m-%d'),
            'type': 'يوم كامل' if ex.type == 'day' else f'{ex.hours} ساعة',
            'note': ex.note or '---'
        } for ex in month_excuses]

        # 5. المعادلة النهائية الشاملة للاستحقاقات والاستقطاعات
        total_income = (u.base_salary or 0) + gross_commission + bonuses
        total_deductions = total_returns_deduction + attendance_deduction + advances + other_penalties

        net_salary = total_income - total_deductions

        # 6. التحقق هل تم صرف الراتب لهذا الشهر مسبقاً (لمنع تكرار الصرف وظهور الزر)
        # نتحقق من الملاحظة فقط (مثلاً "صرف راتب شهر 2026-04") وليس تاريخ التسجيل
        # عشان لو صرفنا راتب مارس يوم 1 أبريل ميتحسبش كأن أبريل اتصرف
        is_paid = db.session.query(HRTransaction).filter(
            HRTransaction.user_id == u.id,
            HRTransaction.type == 'salary_payment',
            HRTransaction.note.like(f"%{month_str}%")
        ).first() is not None

        # تحديد نوع التوزيع للعرض في الجدول
        if u.is_shared_salary:
            dist_type = "مشترك (50/50)"
        else:
            manager_name = "المدير العام"
            if u.manager_id:
                m = db.session.get(User, u.manager_id)
                if m: manager_name = m.fullname
            dist_type = f"خاص ({manager_name})"

        # تجميع البيانات لإرسالها لملف HTML
        employees_data.append({
            'id': u.id,
            'name': u.fullname,
            'dist_type': dist_type, # النوع الجديد
            'base': u.base_salary or 0,
            'season_total': int(total_season_items),
            'commission': round(gross_commission, 2),
            'gross_sales': int(current_month_items),
            'net_sales': int(net_for_tier),
            'same_month_returns': int(same_month_returns_qty),
            'cross_month_returns': int(cross_month_returns_qty),
            'returns_deduction': round(total_returns_deduction, 2),
            'returns_details': returns_details,
            'attendance_deduction': round(attendance_deduction, 2),
            'attendance_details': attendance_details,
            'other_penalties': round(other_penalties, 2),
            'penalties_details': penalties_details,
            'advances': round(advances, 2),
            'advances_details': advances_details,
            'bonuses': round(bonuses, 2),
            'bonuses_details': bonuses_details,
            'net_salary': round(max(0, net_salary), 2),
            'is_paid': is_paid,
            'excuses_count': excuses_count,
            'excuses_details': excuses_details
        })

    # ترتيب القائمة حسب النوع (مشترك أولاً ثم خاص)
    employees_data.sort(key=lambda x: x['dist_type'], reverse=True)

    return render_template('payroll.html', employees=employees_data, month=month_str, accounts=accounts)

@app.route('/debug_users_info')
def debug_users_info():
    users = User.query.all()
    res = []
    for u in users:
        mgr = db.session.get(User, u.manager_id) if u.manager_id else None
        mgr_name = mgr.fullname if mgr else 'None'
        mgr_role = mgr.role if mgr else 'None'
        res.append(f"ID: {u.id} | Name: {u.fullname} | Role: {u.role} | MgrID: {u.manager_id} | MgrName: {mgr_name} | MgrRole: {mgr_role}")
    return "<br>".join(res)
