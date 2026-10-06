import sys
sys.path.append('.')

from backend.app import app, db
from backend.models import User, SaleOrder, ReturnInvoice, PartnerTransaction
from backend.routes.orders import update_monthly_commissions
from datetime import datetime

with app.app_context():
    start_date = datetime(2026, 10, 1)
    
    print("1. Updating commissions for all users in October...")
    users = User.query.all()
    for u in users:
        update_monthly_commissions(u.id, start_date)
        
    print("2. Fixing discount_deduction for October...")
    # Find all orders in October with discount > 0
    orders = SaleOrder.query.filter(SaleOrder.date >= start_date, SaleOrder.discount > 0).all()
    for order in orders:
        seller = order.sales_rep
        if not seller: continue
        
        partners = []
        if getattr(seller, 'partner_group_id', None):
            partners = User.query.filter_by(role='manager', partner_group_id=seller.partner_group_id).all()
        else:
            if seller.role == 'manager': partners = [seller]
            elif seller.manager_id:
                mgr = User.query.get(seller.manager_id)
                if mgr and mgr.role == 'manager': partners = [mgr]
                
        if partners:
            # Delete old discount transactions
            PartnerTransaction.query.filter_by(order_id=order.id, type='discount_deduction').delete()
            
            # Create new ones
            num = len(partners)
            disc_share = float(order.discount) / num
            for p in partners:
                db.session.add(PartnerTransaction(
                    partner_id=p.id,
                    order_id=order.id,
                    type='discount_deduction',
                    amount=-disc_share,
                    description=f"خصم ممنوح للعميل مشتركة ({num}) - فاتورة #{order.id}",
                    date=order.date
                ))
    
    print("3. Fixing return_penalty for October...")
    # Find all returns in October with deduction
    returns = ReturnInvoice.query.filter(ReturnInvoice.date >= start_date).all()
    for ret in returns:
        order = ret.order
        seller = order.sales_rep
        if not seller: continue
        
        partners = []
        if getattr(seller, 'partner_group_id', None):
            partners = User.query.filter_by(role='manager', partner_group_id=seller.partner_group_id).all()
        else:
            if seller.role == 'manager': partners = [seller]
            elif seller.manager_id:
                mgr = User.query.get(seller.manager_id)
                if mgr and mgr.role == 'manager': partners = [mgr]
                
        if partners:
            # Delete old return_penalty
            PartnerTransaction.query.filter_by(order_id=order.id, type='return_penalty').delete()
            
            # Recreate them
            num = len(partners)
            total_deduction = float(ret.total_deduction or 0)
            if total_deduction > 0:
                penalty_amt = total_deduction / num
                for p in partners:
                    db.session.add(PartnerTransaction(
                        partner_id=p.id, order_id=order.id, type='return_penalty',
                        amount=-penalty_amt, description=f"تحمل خسائر مرتجع مشتركة ({num}) فاتورة #{order.id}",
                        date=ret.date
                    ))
                    
    db.session.commit()
    print("Database fix completed successfully.")
