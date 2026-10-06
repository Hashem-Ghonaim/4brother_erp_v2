import re
import os

path = r'd:\Work\WEB\ERP System\Ahmed Abd-Elfattah\4brother_erp_v2\backend\routes\partners.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# I will replace the entire partners_report function.
# It starts at `def partners_report():` and ends at `totals = {`

new_func = """def partners_report():
    if current_user.role not in ['general_manager', 'manager']:
        return "غير مصرح", 403
        
    from ..models import PartnerGroup
    groups = PartnerGroup.query.all()
    report_data = []

    today = cairo_now().date()
    start_date_str = request.args.get('start_date', today.replace(day=1).strftime('%Y-%m-%d'))
    end_date_str = request.args.get('end_date', today.strftime('%Y-%m-%d'))

    grand_total_period = 0
    start_datetime = datetime.strptime(start_date_str, '%Y-%m-%d')
    end_datetime = datetime.strptime(end_date_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59)
    target_month_str = start_datetime.strftime('%Y-%m')

    for g in groups:
        group_partners = User.query.filter_by(role='manager', partner_group_id=g.id).all()
        if not group_partners:
            continue
            
        team_users = User.query.filter_by(partner_group_id=g.id).all()
        team_ids = [u.id for u in team_users]

        total_sold = db.session.query(func.sum(SaleItem.quantity)).join(SaleOrder).filter(
            SaleOrder.user_id.in_(team_ids),
            SaleOrder.is_proforma == False,
            SaleOrder.date >= start_datetime,
            SaleOrder.date <= end_datetime
        ).scalar() or 0

        same_month_ret = db.session.query(func.sum(ReturnInvoice.total_qty)).join(SaleOrder).filter(
            SaleOrder.user_id.in_(team_ids),
            SaleOrder.date >= start_datetime,
            SaleOrder.date <= end_datetime,
            ReturnInvoice.date >= start_datetime,
            ReturnInvoice.date <= end_datetime
        ).scalar() or 0

        cross_month_ret = db.session.query(func.sum(ReturnInvoice.total_qty)).join(SaleOrder).filter(
            SaleOrder.user_id.in_(team_ids),
            SaleOrder.date < start_datetime,
            ReturnInvoice.date >= start_datetime,
            ReturnInvoice.date <= end_datetime
        ).scalar() or 0

        final_pieces = total_sold - same_month_ret - cross_month_ret

        # HR reversed commissions
        sales_rep_comm_reversed_display = 0
        sales_reversed_comm_details = []
        hr_return_reversals = HRTransaction.query.filter(
            HRTransaction.user_id.in_(team_ids),
            HRTransaction.type == 'return_reversal',
            HRTransaction.date >= start_datetime,
            HRTransaction.date <= end_datetime
        ).all()
        for t in hr_return_reversals:
            employee = User.query.get(t.user_id) if t.user_id else None
            user_name = employee.fullname if employee else '---'
            inv_match = re.search(r'#(\d+)', t.note or '')
            order_id = int(inv_match.group(1)) if inv_match else None
            invoice_label = f"فاتورة #{order_id}" if order_id else "---"
            sales_reversed_comm_details.append({
                'amount': abs(t.amount), 'user_name': user_name, 'desc': t.note or '',
                'date': t.date.strftime('%Y-%m-%d'), 'order_id': order_id, 'invoice_label': invoice_label
            })
            sales_rep_comm_reversed_display += abs(t.amount)

        # Cross month return penalty
        cross_month_return_invoices = ReturnInvoice.query.join(SaleOrder).filter(
            SaleOrder.user_id.in_(team_ids),
            SaleOrder.date < start_datetime,
            ReturnInvoice.date >= start_datetime,
            ReturnInvoice.date <= end_datetime
        ).all()
        cross_month_13_deduction = 0
        cross_month_return_details = []
        for ret in cross_month_return_invoices:
            deduction = ret.total_qty * 13
            cross_month_13_deduction -= deduction
            cross_month_return_details.append({
                'amount': -deduction, 'user_name': ret.order.sales_rep.fullname if ret.order.sales_rep else '---',
                'desc': f"مرتجع فاتورة #{ret.order_id} من شهر سابق", 'date': ret.date.strftime('%Y-%m-%d'),
                'order_id': ret.order_id, 'invoice_label': f"فاتورة #{ret.order_id}"
            })

        # Calculate gross_comm_display based on orders
        gross_comm_display = 0
        gross_comm_details_display = []
        period_orders = SaleOrder.query.filter(
            SaleOrder.user_id.in_(team_ids),
            SaleOrder.is_proforma == False,
            SaleOrder.date >= start_datetime,
            SaleOrder.date <= end_datetime
        ).all()
        
        for order in period_orders:
            gross_qty = sum(item.quantity for item in order.items)
            returned_qty = sum(r.total_qty for r in order.return_invoices if r.date.strftime('%Y-%m') == target_month_str)
            net_qty = max(0, gross_qty - returned_qty)
            comm_amount = net_qty * 15
            if comm_amount > 0:
                gross_comm_display += comm_amount
                seller_name = order.sales_rep.fullname if order.sales_rep else "---"
                gross_comm_details_display.append({
                    'amount': comm_amount, 'user_name': seller_name,
                    'desc': f"فاتورة #{order.id} ({gross_qty} قطعة) - {seller_name}",
                    'date': order.date.strftime('%Y-%m-%d'), 'order_id': order.id,
                    'invoice_label': f"فاتورة #{order.id}"
                })

        team_data = {
            'is_team': True,
            'id': f'group_{g.id}',
            'name': g.name,
            'sold_items': final_pieces,
            'sold_details': {
                'total_sold': total_sold,
                'same_month_returns': same_month_ret,
                'cross_month_returns': cross_month_ret,
                'net_pieces': final_pieces
            },
            'gross_comm_display': gross_comm_display,
            'gross_comm_details_display': gross_comm_details_display,
            'sales_rep_comm_reversed_display': sales_rep_comm_reversed_display,
            'sales_comm_reversed_details': sales_reversed_comm_details,
            'returns_details': cross_month_return_details,
            
            'gross_comm': 0.0, 'sales_rep_comm_reversed': 0.0, 'admin_bonus_earned': 0.0, 'admin_penalty_recovered': 0.0,
            'sales_rep_comm': 0.0, 'discounts': 0.0, 'returns': 0.0, 'expenses': 0.0, 'staff_costs': 0.0,
            'admin_bonus_paid': 0.0, 'admin_penalty_deducted': 0.0, 'withdrawals_period': 0.0,
            'period_net_profit': 0.0, 'period_net_cash': 0.0,
            
            'gross_comm_details': [], 'admin_bonus_earned_details': [], 'admin_penalty_recovered_details': [],
            'sales_comm_details': [], 'discounts_details': [], 'expenses_details': [], 'staff_costs_details': [],
            'admin_bonus_paid_details': [], 'admin_penalty_deducted_details': [], 'withdrawals_details': [],
            'partners': []
        }
        
        num_partners = len(group_partners)
        
        for p in group_partners:
            current_balance = db.session.query(func.sum(PartnerTransaction.amount)).filter_by(partner_id=p.id).scalar() or 0.0
            period_trans = PartnerTransaction.query.filter(
                PartnerTransaction.partner_id == p.id,
                PartnerTransaction.date >= start_datetime,
                PartnerTransaction.date <= end_datetime
            ).all()
            
            def build_details(trans_list, custom_filter):
                filtered = [t for t in trans_list if custom_filter(t)]
                res = []
                for t in filtered:
                    desc = t.description or ""
                    share_type = "تحمل 100%"
                    op_type = "مصروف طاقم"
                    if "50%" in desc: share_type = "مشترك (50/50)"
                    if "راتب" in desc or "مرتب" in desc: op_type = "راتب شهرى"
                    user_name = ''
                    if t.order_id:
                        order = SaleOrder.query.get(t.order_id)
                        if order and order.sales_rep: user_name = order.sales_rep.fullname
                    if not user_name:
                        m = re.search(r'عمولة\s*\(([^)]+)\)', desc)
                        if m: user_name = m.group(1)
                    res.append({
                        'amount': t.amount, 'user_name': user_name, 'desc': desc,
                        'share_type': share_type, 'op_type': op_type,
                        'date': t.date.strftime('%Y-%m-%d'), 'order_id': t.order_id,
                        'invoice_label': f"فاتورة #{t.order_id}" if t.order_id else "---"
                    })
                return res

            def safe_float(val):
                if val is None: return 0.0
                if isinstance(val, str): return float(val.replace(',', ''))
                return float(val)

            all_trans = PartnerTransaction.query.filter_by(partner_id=p.id).all()
            total_earned = sum(safe_float(t.amount) for t in all_trans if t.type != 'withdrawal')
            total_withdrawn = sum(safe_float(t.amount) for t in all_trans if t.type == 'withdrawal')

            gross_comm = sum(safe_float(t.amount) for t in period_trans if t.type == 'commission_gross' and safe_float(t.amount) > 0)
            sales_rep_comm = sum(safe_float(t.amount) for t in period_trans if t.type == 'sub_commission' and safe_float(t.amount) < 0)
            sales_rep_comm_reversed = sum(safe_float(t.amount) for t in period_trans if t.type == 'sub_commission' and safe_float(t.amount) > 0)
            discounts = sum(safe_float(t.amount) for t in period_trans if t.type == 'discount_deduction')
            return_penalty = sum(safe_float(t.amount) for t in period_trans if t.type == 'return_penalty')
            expenses = sum(safe_float(t.amount) for t in period_trans if t.type == 'expense_share')
            staff_costs = sum(safe_float(t.amount) for t in period_trans if t.type == 'staff_expense')
            withdrawals_period = sum(safe_float(t.amount) for t in period_trans if t.type == 'withdrawal')
            admin_bonus_earned = sum(safe_float(t.amount) for t in period_trans if t.type == 'admin_bonus' and safe_float(t.amount) > 0)
            admin_bonus_paid = sum(safe_float(t.amount) for t in period_trans if t.type == 'admin_bonus' and safe_float(t.amount) <= 0)
            admin_penalty_recovered = sum(safe_float(t.amount) for t in period_trans if t.type == 'admin_penalty' and safe_float(t.amount) > 0)
            admin_penalty_deducted = sum(safe_float(t.amount) for t in period_trans if t.type == 'admin_penalty' and safe_float(t.amount) <= 0)

            partner_cross_month_13 = cross_month_13_deduction / num_partners
            partner_returns = partner_cross_month_13 + return_penalty
            partner_gross_display = gross_comm_display / num_partners
            partner_sales_reversed_display = sales_rep_comm_reversed_display / num_partners

            period_net_profit = (partner_gross_display + admin_bonus_earned + admin_penalty_recovered + 
                                 partner_sales_reversed_display +
                                 sales_rep_comm + discounts + partner_returns + expenses + staff_costs + 
                                 admin_bonus_paid + admin_penalty_deducted)
            period_net_cash = period_net_profit + withdrawals_period
            
            grand_total_period += period_net_cash

            partner_data = {
                'id': p.id,
                'name': p.fullname,
                'sold_items': '-',
                'sold_details': team_data['sold_details'],
                'gross_comm': round(gross_comm, 2),
                'gross_comm_display': round(partner_gross_display, 2),
                'gross_comm_details_display': team_data['gross_comm_details_display'],
                'sales_rep_comm_reversed': round(sales_rep_comm_reversed, 2),
                'sales_rep_comm_reversed_display': round(partner_sales_reversed_display, 2),
                'admin_bonus_earned': round(admin_bonus_earned, 2),
                'admin_penalty_recovered': round(admin_penalty_recovered, 2),
                'sales_rep_comm': round(sales_rep_comm, 2),
                'discounts': round(discounts, 2),
                'returns': round(partner_returns, 2),
                'expenses': round(expenses, 2),
                'staff_costs': round(staff_costs, 2),
                'admin_bonus_paid': round(admin_bonus_paid, 2),
                'admin_penalty_deducted': round(admin_penalty_deducted, 2),
                
                'gross_comm_details': build_details(period_trans, lambda t: t.type == 'commission_gross' and safe_float(t.amount) > 0),
                'admin_bonus_earned_details': build_details(period_trans, lambda t: t.type == 'admin_bonus' and safe_float(t.amount) > 0),
                'admin_penalty_recovered_details': build_details(period_trans, lambda t: t.type == 'admin_penalty' and safe_float(t.amount) > 0),
                'sales_comm_reversed_details': team_data['sales_comm_reversed_details'],
                'sales_comm_details': team_data['sales_comm_details'], # Need to build this properly
                'discounts_details': build_details(period_trans, lambda t: t.type == 'discount_deduction'),
                'returns_details': team_data['returns_details'] + build_details(period_trans, lambda t: t.type == 'return_penalty'),
                'expenses_details': build_details(period_trans, lambda t: t.type == 'expense_share'),
                'staff_costs_details': build_details(period_trans, lambda t: t.type == 'staff_expense'),
                'admin_bonus_paid_details': build_details(period_trans, lambda t: t.type == 'admin_bonus' and safe_float(t.amount) <= 0),
                'admin_penalty_deducted_details': build_details(period_trans, lambda t: t.type == 'admin_penalty' and safe_float(t.amount) <= 0),
                'withdrawals_details': build_details(period_trans, lambda t: t.type == 'withdrawal'),

                'period_net_profit': round(period_net_profit, 2),
                'withdrawals_period': round(abs(withdrawals_period), 2),
                'period_net_cash': round(period_net_cash, 2),
                
                'total_earned': round(total_earned, 2),
                'total_withdrawn': round(abs(total_withdrawn), 2),
                'current_balance': round(current_balance, 2)
            }
            
            # Rebuild sales_comm_details for partner
            sales_comm_details_built = []
            for t in period_trans:
                if t.type == 'sub_commission' and safe_float(t.amount) < 0:
                    desc = t.description or ''
                    user_name = ''
                    m = re.search(r'عمولة\s*\(([^)]+)\)', desc)
                    if m: user_name = m.group(1)
                    if not user_name and t.order_id:
                        order = SaleOrder.query.get(t.order_id)
                        if order and order.sales_rep: user_name = order.sales_rep.fullname

                    gross_qty = 0
                    returned_qty = 0
                    if t.order_id:
                        order = SaleOrder.query.get(t.order_id)
                        if order:
                            gross_qty = sum(item.quantity for item in order.items)
                            returned_qty = sum(r.total_qty for r in order.return_invoices if r.date.strftime('%Y-%m') == target_month_str) if order.return_invoices else 0

                    net_qty = max(0, gross_qty - returned_qty)
                    rate = abs(t.amount) / net_qty if net_qty > 0 else 0
                    sales_comm_details_built.append({
                        'amount': round(net_qty * rate, 2), 'user_name': user_name, 'gross_qty': gross_qty,
                        'returned_qty': returned_qty, 'desc': desc, 'date': t.date.strftime('%Y-%m-%d'),
                        'order_id': t.order_id, 'invoice_label': f"فاتورة #{t.order_id}" if t.order_id else "---"
                    })
            partner_data['sales_comm_details'] = sales_comm_details_built

            team_data['partners'].append(partner_data)

            # Accumulate team totals
            team_data['gross_comm'] += partner_data['gross_comm']
            team_data['sales_rep_comm_reversed'] += partner_data['sales_rep_comm_reversed']
            team_data['admin_bonus_earned'] += partner_data['admin_bonus_earned']
            team_data['admin_penalty_recovered'] += partner_data['admin_penalty_recovered']
            team_data['sales_rep_comm'] += partner_data['sales_rep_comm']
            team_data['discounts'] += partner_data['discounts']
            team_data['returns'] += partner_data['returns']
            team_data['expenses'] += partner_data['expenses']
            team_data['staff_costs'] += partner_data['staff_costs']
            team_data['admin_bonus_paid'] += partner_data['admin_bonus_paid']
            team_data['admin_penalty_deducted'] += partner_data['admin_penalty_deducted']
            team_data['withdrawals_period'] += partner_data['withdrawals_period']
            team_data['period_net_profit'] += partner_data['period_net_profit']
            team_data['period_net_cash'] += partner_data['period_net_cash']

            # Aggregate details to team level for popups
            team_data['gross_comm_details'].extend(partner_data['gross_comm_details'])
            team_data['admin_bonus_earned_details'].extend(partner_data['admin_bonus_earned_details'])
            team_data['admin_penalty_recovered_details'].extend(partner_data['admin_penalty_recovered_details'])
            team_data['sales_comm_details'].extend(partner_data['sales_comm_details'])
            team_data['discounts_details'].extend(partner_data['discounts_details'])
            # team_data['returns_details'] already has cross_month_return_details, just add return_penalty
            team_data['returns_details'].extend([t for t in partner_data['returns_details'] if 'مرتجع فاتورة' not in t['desc']])
            team_data['expenses_details'].extend(partner_data['expenses_details'])
            team_data['staff_costs_details'].extend(partner_data['staff_costs_details'])
            team_data['admin_bonus_paid_details'].extend(partner_data['admin_bonus_paid_details'])
            team_data['admin_penalty_deducted_details'].extend(partner_data['admin_penalty_deducted_details'])
            team_data['withdrawals_details'].extend(partner_data['withdrawals_details'])
        
        # Round team totals
        team_data['gross_comm'] = round(team_data['gross_comm'], 2)
        team_data['sales_rep_comm_reversed'] = round(team_data['sales_rep_comm_reversed'], 2)
        team_data['admin_bonus_earned'] = round(team_data['admin_bonus_earned'], 2)
        team_data['admin_penalty_recovered'] = round(team_data['admin_penalty_recovered'], 2)
        team_data['sales_rep_comm'] = round(team_data['sales_rep_comm'], 2)
        team_data['discounts'] = round(team_data['discounts'], 2)
        team_data['returns'] = round(team_data['returns'], 2)
        team_data['expenses'] = round(team_data['expenses'], 2)
        team_data['staff_costs'] = round(team_data['staff_costs'], 2)
        team_data['admin_bonus_paid'] = round(team_data['admin_bonus_paid'], 2)
        team_data['admin_penalty_deducted'] = round(team_data['admin_penalty_deducted'], 2)
        team_data['withdrawals_period'] = round(team_data['withdrawals_period'], 2)
        team_data['period_net_profit'] = round(team_data['period_net_profit'], 2)
        team_data['period_net_cash'] = round(team_data['period_net_cash'], 2)

        report_data.append(team_data)
"""

start_idx = content.find("def partners_report():")
end_idx = content.find("    totals = {")

new_content = content[:start_idx] + new_func + content[end_idx:]

with open(path, 'w', encoding='utf-8') as f:
    f.write(new_content)

print("Updated partners.py")
