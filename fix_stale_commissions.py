"""
Fix stale commission records caused by order.return_invoices relationship cache.

The bug: when a return was processed, update_monthly_commissions read order.return_invoices
which returned a stale list (cached before the new ReturnInvoice was added). This caused
commissions to be calculated using gross quantity without deducting returns.

This script recalculates ALL monthly commissions from scratch for all affected sales reps.
Run once after deploying the code fix.
"""
import sys
from collections import defaultdict
from datetime import datetime, timedelta
sys.path.insert(0, '.')

from backend.core import app, db
from backend.models import User, SaleOrder, SaleItem, ReturnInvoice, PartnerTransaction
from backend.routes.invoices import update_monthly_commissions


def find_affected_months():
    """Find all (user_id, year-month) pairs that may have stale commissions."""
    rows = db.session.query(
        SaleOrder.user_id,
        SaleOrder.date
    ).filter(
        SaleOrder.is_proforma == False,
        SaleOrder.id.in_(
            db.session.query(ReturnInvoice.order_id).distinct()
        )
    ).distinct().all()

    affected = set()
    for user_id, date in rows:
        affected.add((user_id, date.strftime('%Y-%m'), date))
    return list(affected)


with app.app_context():
    affected = find_affected_months()
    print(f"Found {len(affected)} potentially affected (user, month) pairs")

    fixed_count = 0
    error_count = 0

    for user_id, month_str, ref_date in affected:
        user = db.session.get(User, user_id)
        if not user or user.role not in ('sales', 'worker'):
            continue

        print(f"\n[{month_str}] {user.fullname or user_id}...", end=' ')
        try:
            update_monthly_commissions(user_id, ref_date)
            fixed_count += 1
            print("✅")
        except Exception as e:
            db.session.rollback()
            print(f"❌ Error: {e}")
            error_count += 1

    print(f"\n{'='*50}")
    print(f"Done. Fixed: {fixed_count}, Errors: {error_count}")
