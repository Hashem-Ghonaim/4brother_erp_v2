"""
Shared helper functions for route modules.
"""
import math
import json
import re
from functools import wraps
from datetime import datetime, date, timedelta
from flask import request, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import func

from .core import app, db, cairo_now, FACTORY_LAT, FACTORY_LNG, ALLOWED_RADIUS
from .models import (
    User, Customer, SaleOrder, SaleItem, SaleOrder, PartnerTransaction,
    HRTransaction, ReturnInvoice
)


def general_manager_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role not in ('general_manager', 'owner'):
            return "\u063a\u064a\u0631 \u0645\u0635\u0631\u062d (\u0627\u0644\u0645\u062f\u064a\u0631 \u0627\u0644\u0639\u0627\u0645 \u0641\u0642\u0637)", 403
        return f(*args, **kwargs)
    return decorated_function


def permission_required(perm_name):
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if not current_user.has_perm(perm_name):
                flash(f'\u0639\u0641\u0648\u0627\u064b\u060c \u0644\u064a\u0633 \u0644\u062f\u064a\u0643 \u0635\u0644\u0627\u062d\u064a\u0629: {perm_name}', 'danger')
                return redirect(request.referrer or url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def permission_required_any(*perm_names):
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if not any(current_user.has_perm(p) for p in perm_names) and current_user.role not in ('general_manager', 'owner'):
                flash('\u0639\u0641\u0648\u0627\u064b\u060c \u0644\u064a\u0633 \u0644\u062f\u064a\u0643 \u0627\u0644\u0635\u0644\u0627\u062d\u064a\u0627\u062a \u0627\u0644\u0645\u0637\u0644\u0648\u0628\u0629.', 'danger')
                return redirect(request.referrer or url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371e3
    phi1 = lat1 * math.pi / 180
    phi2 = lat2 * math.pi / 180
    delta_phi = (lat2 - lat1) * math.pi / 180
    delta_lambda = (lon2 - lon1) * math.pi / 180
    a = math.sin(delta_phi/2) * math.sin(delta_phi/2) + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda/2) * math.sin(delta_lambda/2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c



def calculate_user_commission(user, quantity_to_pay, total_monthly_context=None):
    """
    quantity_to_pay: عدد القطع اللي عايزين نحسب فلوسها (مثلاً 50 قطعة في الفاتورة دي)
    total_monthly_context: إجمالي مبيعات البنت في الشهر كله (عشان نحدد الشريحة)
    """
    commission = 0.0

    # لو لم يتم تمرير الإجمالي، نعتبره هو نفس الكمية الحالية (للحماية)
    if total_monthly_context is None:
        total_monthly_context = quantity_to_pay

    # 1. نظام الشرائح التراكمي (Tiered Sales)
    if user.job_type == 'tiered_sales' and user.commission_rules:
        try:
            tiers = json.loads(user.commission_rules)
            selected_tier_val = 0.0

            # بنلف على الشرائح عشان نشوف "الإجمالي الشهري" يقع فين
            for tier in tiers:
                tier_min = float(tier.get('min', 0))
                tier_max = float(tier.get('max', 999999))
                tier_val = float(tier.get('val', 0)) # سعر القطعة في الشريحة دي

                # هنا بنقارن "الإجمالي الشهري" مش فاتورة دلوقتي بس
                if tier_min <= total_monthly_context <= tier_max:
                    selected_tier_val = tier_val
                    break

            # بعد ما عرفنا سعر القطعة المناسب لمجهودها الشهري، نضربه في عدد قطع الفاتورة
            if selected_tier_val > 0:
                commission += quantity_to_pay * selected_tier_val

        except Exception as e:
            print(f"Error calculating tiers: {e}")

    # 2. العمولة الثابتة (لو مفيش شرائح)
    elif user.commission_value and user.commission_value > 0:
        commission += quantity_to_pay * user.commission_value

    return commission

def calculate_team_leader_bonus(user_id, start_dt, end_dt):
    """
    يحسب بونص الإدارة (1 جنيه على كل قطعة صافية مباعة من خلال الفريق)
    """
    team = User.query.filter_by(manager_id=user_id).all()
    if not team:
        return 0.0
    
    total_bonus = 0.0
    for member in team:
        gross_items = db.session.query(func.sum(SaleItem.quantity)).join(SaleOrder).filter(
            SaleOrder.user_id == member.id, 
            SaleOrder.is_proforma == False, 
            SaleOrder.date >= start_dt, 
            SaleOrder.date < end_dt
        ).scalar() or 0
        
        # المرتجعات اللي حصلت في نفس الشهر لنفس الموظف
        returned_items = db.session.query(func.sum(ReturnInvoice.total_qty)).join(SaleOrder).filter(
            SaleOrder.user_id == member.id,
            ReturnInvoice.date >= start_dt,
            ReturnInvoice.date < end_dt
        ).scalar() or 0
        
        net_items = max(0, gross_items - returned_items)
        total_bonus += (net_items * 1.0) # 1 جنيه على كل قطعة
        
    return total_bonus

def get_accessible_users():
    """
    ترجع قائمة بمعرفات المستخدمين (IDs) الذين يحق للمستخدم الحالي رؤية بياناتهم.
    """
    # المدير العام أو الموظفة الخاصة EMP201 يشوفوا الكل
    if current_user.role == 'general_manager' or current_user.emp_code == 'EMP201':
        return [u.id for u in User.query.all()]

    # إذا كان المستخدم ينتمي إلى مجموعة (فريق)، يمكنه رؤية جميع أعضاء الفريق
    if current_user.partner_group_id:
        team = User.query.filter_by(partner_group_id=current_user.partner_group_id).all()
        return [u.id for u in team]

    # المنطق القديم لو مفيش فريق
    elif current_user.role == 'manager':
        team = User.query.filter_by(manager_id=current_user.id).all()
        return [current_user.id] + [u.id for u in team]

    else:
        return [current_user.id]

def get_allowed_customers():
    if current_user.role in ['general_manager', 'owner', 'partner'] or current_user.emp_code == 'EMP201':
        return Customer.query.order_by(Customer.id.desc()).all()
    
    if current_user.partner_group_id:
        team_ids = [u.id for u in User.query.filter_by(partner_group_id=current_user.partner_group_id).all()]
        return Customer.query.filter(Customer.created_by_id.in_(team_ids)).order_by(Customer.id.desc()).all()

    elif current_user.role == 'manager':
        subordinates_ids = [u.id for u in current_user.subordinates]
        subordinates_ids.append(current_user.id)
        return Customer.query.filter(Customer.created_by_id.in_(subordinates_ids)).order_by(Customer.id.desc()).all()
    else:
        return Customer.query.filter_by(created_by_id=current_user.id).order_by(Customer.id.desc()).all()
