from backend.core import app, db
from backend.models import User, SaleOrder, SaleItem, ReturnInvoice, PartnerTransaction
from backend.helpers import calculate_user_commission
from sqlalchemy import func

def run_fix():
    with app.app_context():
        returns = ReturnInvoice.query.all()
        created = 0
        for r in returns:
            order = SaleOrder.query.get(r.order_id)
            if not order or not order.user_id: continue
            
            # التأكد إذا كان المرتجع ليه حركات مالية مسجلة من قبل أم لا
            pts = PartnerTransaction.query.filter_by(order_id=r.order_id, type='commission_gross').all()
            has_negative = any(pt.amount < 0 for pt in pts)
            
            if not has_negative:
                sales_rep = User.query.get(order.user_id)
                partner = None
                if sales_rep.role == 'manager':
                    partner = sales_rep
                elif sales_rep.manager_id:
                    mgr = User.query.get(sales_rep.manager_id)
                    if mgr and mgr.role == 'manager': partner = mgr
                    
                if partner:
                    print(f"Fixing Return #{r.id} for Order #{order.id} (Partner: {partner.fullname})")
                    
                    # 1. خصم الـ 13 جنيه من ربح الشريك
                    qty = r.total_qty or 0
                    if qty > 0:
                        db.session.add(PartnerTransaction(
                            partner_id=partner.id, order_id=order.id, type='commission_gross',
                            amount=-(qty * 13.0),
                            description=f"خصم ربح قطع مرتجعة ({qty} قطعة) - فاتورة #{order.id} (بأثر رجعي)",
                            date=r.date
                        ))
                    
                    # 2. خصم أي خسائر شحن أو توالف من الشريك
                    deduction = r.total_deduction or 0
                    if deduction > 0:
                        db.session.add(PartnerTransaction(
                            partner_id=partner.id, order_id=order.id, type='return_penalty',
                            amount=-deduction,
                            description=f"تحمل خسائر مرتجع فاتورة #{order.id} (بأثر رجعي)",
                            date=r.date
                        ))
                    
                    # ملاحظة: تم إزالة إضافة sub_commission بناء على التعديلات الأخيرة التي تتجاهل العمولة المستردة
                    
                    created += 1

        if created > 0:
            db.session.commit()
            print(f"Success! Migrated {created} missing returns to partner transactions.")
        else:
            print("No missing returns found. Database is already up to date.")

if __name__ == '__main__':
    run_fix()
