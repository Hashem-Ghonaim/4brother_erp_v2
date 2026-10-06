import os
import re

def process_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. Fix update_monthly_commissions
    old_update = """        partner = None
        if sales_rep.role == 'manager':
            partner = sales_rep
        elif sales_rep.manager_id:
            partner = User.query.get(sales_rep.manager_id)

        if not partner or partner.role != 'manager': return"""

    new_update = """        partners = []
        if getattr(sales_rep, 'partner_group_id', None):
            partners = User.query.filter_by(role='manager', partner_group_id=sales_rep.partner_group_id).all()
        else:
            if sales_rep.role == 'manager':
                partners = [sales_rep]
            elif sales_rep.manager_id:
                mgr = User.query.get(sales_rep.manager_id)
                if mgr and mgr.role == 'manager':
                    partners = [mgr]

        if not partners: return
        num_partners = len(partners)"""
    
    content = content.replace(old_update, new_update)

    # Now replace the loop inside update_monthly_commissions
    old_loop1 = """        # 5. حذف التسويات القديمة (اللي ملهاش order_id) الخاصة بالشهر ده
        PartnerTransaction.query.filter(
            PartnerTransaction.partner_id == partner.id,
            PartnerTransaction.type == 'sub_commission',
            PartnerTransaction.order_id == None,
            PartnerTransaction.description.like(f"%{sales_rep.fullname}%"),
            PartnerTransaction.date >= target_month_start,
            PartnerTransaction.date < next_month
        ).delete(synchronize_session=False)"""
    new_loop1 = """        # 5. حذف التسويات القديمة
        for p in partners:
            PartnerTransaction.query.filter(
                PartnerTransaction.partner_id == p.id,
                PartnerTransaction.type == 'sub_commission',
                PartnerTransaction.order_id == None,
                PartnerTransaction.description.like(f"%{sales_rep.fullname}%"),
                PartnerTransaction.date >= target_month_start,
                PartnerTransaction.date < next_month
            ).delete(synchronize_session=False)"""
    content = content.replace(old_loop1, new_loop1)

    old_loop2 = """            # ج) عمولة الشريك (Gross) - من البروفايل
            partner_rate = float(partner.commission_value or 13.0)
            db.session.add(PartnerTransaction(
                partner_id=partner.id,
                order_id=order.id,
                type='commission_gross',
                amount=net_qty * partner_rate,
                description=f"عمولة ({net_qty} قطعة × {partner_rate}) - فاتورة مبيعات ({sales_rep.fullname})",
                date=order.date
            ))

            # د) عمولة الموظفة (تتخصم من الشريك)
            if sales_rep.id != partner.id and rate_per_item > 0:
                girl_comm = net_qty * rate_per_item
                total_month_comm += girl_comm
                
                db.session.add(PartnerTransaction(
                    partner_id=partner.id,
                    order_id=order.id,
                    type='sub_commission',
                    amount=-girl_comm,
                    description=f"عمولة ({sales_rep.fullname}) - شهر {target_month_str} ({total_monthly_items} قطعة، فئة {rate_per_item})",
                    date=order.date
                ))"""
    
    new_loop2 = """            # ج) عمولة الشريك (Gross) - من البروفايل
            partner_rate = float(partners[0].commission_value or 13.0)
            gross_amt = (net_qty * partner_rate) / num_partners
            for p in partners:
                db.session.add(PartnerTransaction(
                    partner_id=p.id,
                    order_id=order.id,
                    type='commission_gross',
                    amount=gross_amt,
                    description=f"عمولة ({net_qty} قطعة × {partner_rate}) مشتركة ({num_partners}) - فاتورة مبيعات ({sales_rep.fullname})",
                    date=order.date
                ))

            # د) عمولة الموظفة (تتخصم من الشريك)
            if (not any(p.id == sales_rep.id for p in partners)) and rate_per_item > 0:
                girl_comm = net_qty * rate_per_item
                total_month_comm += girl_comm
                sub_amt = girl_comm / num_partners
                
                for p in partners:
                    db.session.add(PartnerTransaction(
                        partner_id=p.id,
                        order_id=order.id,
                        type='sub_commission',
                        amount=-sub_amt,
                        description=f"عمولة ({sales_rep.fullname}) مشتركة ({num_partners}) - شهر {target_month_str} ({total_monthly_items} قطعة، فئة {rate_per_item})",
                        date=order.date
                    ))"""
    
    content = content.replace(old_loop2, new_loop2)
    
    # discount_deductions in orders.py and invoices.py
    old_discount = """        # تحديد الشريك المسؤول (المدير المباشر)
        partner = None
        if seller_user.role == 'manager': partner = seller_user
        elif seller_user.manager_id:
            mgr = User.query.get(seller_user.manager_id)
            if mgr and mgr.role == 'manager': partner = mgr

        # 1. خصم التخفيض من الشريك (لو فيه خصم)
        if partner and discount > 0:
            db.session.add(PartnerTransaction(
                partner_id=partner.id,
                order_id=order.id,
                type='discount_deduction',
                amount=-discount,
                description=f"خصم ممنوح للعميل - فاتورة #{order.id}"
            ))"""
    new_discount = """        # تحديد الشركاء (لو في جروب يبقي كل الشركاء)
        partners = []
        if getattr(seller_user, 'partner_group_id', None):
            partners = User.query.filter_by(role='manager', partner_group_id=seller_user.partner_group_id).all()
        else:
            if seller_user.role == 'manager': partners = [seller_user]
            elif seller_user.manager_id:
                mgr = User.query.get(seller_user.manager_id)
                if mgr and mgr.role == 'manager': partners = [mgr]

        # 1. خصم التخفيض من الشركاء (لو فيه خصم)
        if partners and discount > 0:
            num = len(partners)
            disc_share = discount / num
            for p in partners:
                db.session.add(PartnerTransaction(
                    partner_id=p.id,
                    order_id=order.id,
                    type='discount_deduction',
                    amount=-disc_share,
                    description=f"خصم ممنوح للعميل مشتركة ({num}) - فاتورة #{order.id}"
                ))"""
    content = content.replace(old_discount, new_discount)

    # returns.py replacements
    old_ret = """            # 6. معالجة حسابات الشركاء (إلغاء الربح والعمولة عن القطع المرتجعة)
            sales_rep = User.query.get(order.user_id)
            partner = None
            if sales_rep.role == 'manager': partner = sales_rep
            elif sales_rep.manager_id:
                mgr = User.query.get(sales_rep.manager_id)
                if mgr and mgr.role == 'manager': partner = mgr

            if partner:
                # أ) إلغاء ربح الشريك عن القطع المرجعة
                partner_rate = float(partner.commission_value or 13.0)
                db.session.add(PartnerTransaction(
                    partner_id=partner.id, order_id=order.id, type='commission_gross',
                    amount=-(total_qty_returned * partner_rate),
                    description=f"خصم ربح قطع مرتجعة ({total_qty_returned} قطعة × {partner_rate}) - فاتورة #{order.id}"
                ))
                # ب) خصم خسائر الشحن أو التوالف من الشريك
                if total_deduction > 0:
                    db.session.add(PartnerTransaction(
                        partner_id=partner.id, order_id=order.id, type='return_penalty',
                        amount=-total_deduction, description=f"تحمل خسائر مرتجع فاتورة #{order.id}"
                    ))
                # ج) استرداد عمولة السيلز (ترجع لجيب المدير)
                if sales_rep.role in ('sales', 'sales_manager'):"""
    new_ret = """            # 6. معالجة حسابات الشركاء (إلغاء الربح والعمولة عن القطع المرتجعة)
            sales_rep = User.query.get(order.user_id)
            partners = []
            if getattr(sales_rep, 'partner_group_id', None):
                partners = User.query.filter_by(role='manager', partner_group_id=sales_rep.partner_group_id).all()
            else:
                if sales_rep.role == 'manager': partners = [sales_rep]
                elif sales_rep.manager_id:
                    mgr = User.query.get(sales_rep.manager_id)
                    if mgr and mgr.role == 'manager': partners = [mgr]

            if partners:
                num_partners = len(partners)
                # أ) إلغاء ربح الشريك عن القطع المرجعة
                partner_rate = float(partners[0].commission_value or 13.0)
                deduction_amt = (total_qty_returned * partner_rate) / num_partners
                for p in partners:
                    db.session.add(PartnerTransaction(
                        partner_id=p.id, order_id=order.id, type='commission_gross',
                        amount=-deduction_amt,
                        description=f"خصم ربح قطع مرتجعة ({total_qty_returned} قطعة × {partner_rate}) مشتركة ({num_partners}) - فاتورة #{order.id}"
                    ))
                # ب) خصم خسائر الشحن أو التوالف من الشريك
                if total_deduction > 0:
                    penalty_amt = total_deduction / num_partners
                    for p in partners:
                        db.session.add(PartnerTransaction(
                            partner_id=p.id, order_id=order.id, type='return_penalty',
                            amount=-penalty_amt, description=f"تحمل خسائر مرتجع مشتركة ({num_partners}) فاتورة #{order.id}"
                        ))
                # ج) استرداد عمولة السيلز (ترجع لجيب المدير)
                if sales_rep.role in ('sales', 'sales_manager'):"""
    content = content.replace(old_ret, new_ret)

    old_ret_sub = """                            db.session.add(PartnerTransaction(
                                partner_id=partner.id, order_id=order.id, type='sub_commission',
                                amount=(total_qty_returned * rate_per_item),
                                description=f"استرداد عمولة ({sales_rep.fullname}) لمرتجع - فاتورة #{order.id}"
                            ))"""
    new_ret_sub = """                            comm_reversal = (total_qty_returned * rate_per_item) / num_partners
                            for p in partners:
                                db.session.add(PartnerTransaction(
                                    partner_id=p.id, order_id=order.id, type='sub_commission',
                                    amount=comm_reversal,
                                    description=f"استرداد عمولة ({sales_rep.fullname}) لمرتجع مشتركة ({num_partners}) - فاتورة #{order.id}"
                                ))"""
    content = content.replace(old_ret_sub, new_ret_sub)

    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

base = r'd:\Work\WEB\ERP System\Ahmed Abd-Elfattah\4brother_erp_v2\backend\routes'
process_file(os.path.join(base, 'orders.py'))
process_file(os.path.join(base, 'invoices.py'))
process_file(os.path.join(base, 'returns.py'))

print("Source files updated.")
