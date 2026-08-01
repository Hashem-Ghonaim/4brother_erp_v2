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
        if current_user.role != 'general_manager':
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
            if not any(current_user.has_perm(p) for p in perm_names) and current_user.role != 'general_manager':
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

def get_accessible_users():
    """
    ترجع قائمة بمعرفات المستخدمين (IDs) الذين يحق للمستخدم الحالي رؤية بياناتهم.
    """
    # المدير العام أو الموظفة الخاصة EMP201 يشوفوا الكل
    if current_user.role == 'general_manager' or current_user.emp_code == 'EMP201':
        return [u.id for u in User.query.all()]

    elif current_user.role == 'manager':
        # مدير الفريق يرى نفسه + الموظفين الذين يدارون من قبله
        team = User.query.filter_by(manager_id=current_user.id).all()
        return [current_user.id] + [u.id for u in team]

    else:
        # الموظف العادي يرى بياناته هو فقط
        return [current_user.id]
# دالة العملاء (نزلتها زي ما هي بالظبط عشان متتأثرش)

def get_allowed_customers():
    if current_user.role in ['general_manager', 'partner']:
        return Customer.query.order_by(Customer.id.desc()).all()
    elif current_user.role == 'manager':
        subordinates_ids = [u.id for u in current_user.subordinates]
        subordinates_ids.append(current_user.id)
        return Customer.query.filter(Customer.created_by_id.in_(subordinates_ids)).order_by(Customer.id.desc()).all()
    else:
        return Customer.query.filter_by(created_by_id=current_user.id).order_by(Customer.id.desc()).all()
