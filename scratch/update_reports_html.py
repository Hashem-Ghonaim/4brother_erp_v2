import os

path = r'd:\Work\WEB\ERP System\Ahmed Abd-Elfattah\4brother_erp_v2\templates\partners_report.html'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# I need to replace the tbody logic.
start_idx = content.find("                    <tbody>")
end_idx = content.find("                    <tfoot>")

tbody_content = """                    <tbody>
                        {% for row in report %}
                        <!-- Team Row -->
                        <tr style="background-color: #f1f5f9; cursor: pointer; border-top: 2px solid #dee2e6;" onclick="toggleTeam('{{ row.id }}')">
                            <td class="text-start ps-4 fw-bold text-primary">
                                <i class="fas fa-chevron-down ms-1 text-muted" id="icon-{{ row.id }}"></i> {{ row.name }}
                            </td>

                            <!-- Revenue -->
                            <td class="drilldown-cell " data-name="{{ row.name }}" data-sold='{{ row.sold_details | tojson | forceescape }}'>
                                <span class="sold-badge">{{ row.sold_items }} قطعة</span>
                            </td>
                            <td class="val-pos " data-name="{{ row.name }}" data-gross-details='{{ row.gross_comm_details_display | tojson | forceescape }}'>
                               {{ row.gross_comm_display }}
                            </td>
                            <td class="val-pos " data-name="{{ row.name }}" data-reversed-comm-details='{{ row.sales_comm_reversed_details | tojson | forceescape }}'>
                               {{ row.sales_rep_comm_reversed_display }}
                            </td>
                            <td class="drilldown-cell val-pos " data-name="{{ row.name }}" data-label="مكافآت مستحقة" data-details="{{ row.admin_bonus_earned_details | tojson | forceescape }}">
                               {{ row.admin_bonus_earned }}
                            </td>
                            <td class="drilldown-cell val-pos " data-name="{{ row.name }}" data-label="جزاءات مستردة" data-details="{{ row.admin_penalty_recovered_details | tojson | forceescape }}">
                               {{ row.admin_penalty_recovered }}
                            </td>

                            <!-- Deductions -->
                            <td class="{{ 'val-neg' if row.sales_rep_comm < 0 else 'val-neutral' }}" data-name="{{ row.name }}" data-sales-comm-details='{{ row.sales_comm_details | tojson | forceescape }}'>
                               {{ row.sales_rep_comm }}
                            </td>
                            <td class="{{ 'val-neg' if row.returns < 0 else 'val-neutral' }}" data-name="{{ row.name }}" data-returns-details='{{ row.returns_details | tojson | forceescape }}'>
                               {{ row.returns }}
                            </td>
                            <td class="drilldown-cell {{ 'val-neg' if row.admin_penalty_deducted < 0 else 'val-neutral' }}" data-name="{{ row.name }}" data-label="جزاءات عالشريك" data-details="{{ row.admin_penalty_deducted_details | tojson | forceescape }}">
                               {{ row.admin_penalty_deducted }}
                            </td>
                            <td class="drilldown-cell {{ 'val-neg' if row.admin_bonus_paid < 0 else 'val-neutral' }}" data-name="{{ row.name }}" data-label="مكافآت مدفوعة" data-details="{{ row.admin_bonus_paid_details | tojson | forceescape }}">
                               {{ row.admin_bonus_paid }}
                            </td>
                            <td class="drilldown-cell {{ 'val-neg' if row.discounts < 0 else 'val-neutral' }}" data-name="{{ row.name }}" data-label="خصم فواتير" data-details="{{ row.discounts_details | tojson | forceescape }}">
                               {{ row.discounts }}
                            </td>
                            <td class="drilldown-cell {{ 'val-neg' if row.staff_costs < 0 else 'val-neutral' }}" data-name="{{ row.name }}" data-label="مصاريف طاقم" data-details="{{ row.staff_costs_details | tojson | forceescape }}">
                               {{ row.staff_costs }}
                            </td>
                            <td class="drilldown-cell {{ 'val-neg' if row.expenses < 0 else 'val-neutral' }}" data-name="{{ row.name }}" data-label="مصاريف أخرى" data-details="{{ row.expenses_details | tojson | forceescape }}">
                               {{ row.expenses }}
                            </td>

                            <!-- Withdrawals -->
                            <td class="text-danger fw-bold drilldown-cell" data-name="{{ row.name }}" data-label="المسحوبات" data-details="{{ row.withdrawals_details | tojson | forceescape }}">
                               {{ row.withdrawals_period }}
                            </td>

                            <!-- Net -->
                            <td class="{{ 'profit-positive' if row.period_net_cash >= 0 else 'profit-negative' }} drilldown-formula" data-name="{{ row.name }}" data-label="صافي الربح المستحق (المعادلة)" data-formula='[{"label":"صافي الأرباح","value":{{ row.period_net_profit }}},{"label":"المسحوبات","value":-{{ row.withdrawals_period }}}]' data-result="{{ row.period_net_cash }}">
                               {{ row.period_net_cash }} ج.م
                            </td>
                        </tr>
                        
                        <!-- Partners in Team -->
                        {% for p in row.partners %}
                        <tr class="partner-row-{{ row.id }}" style="display: none; background-color: #fafafa;">
                            <td class="text-start ps-5 fw-bold text-dark"><i class="fas fa-user-tie ms-2 text-secondary"></i> {{ p.name }}</td>

                            <!-- Revenue -->
                            <td class="text-muted"><span class="small">-</span></td>
                            <td class="val-pos " style="cursor:pointer" data-name="{{ p.name }}" data-gross-details='{{ p.gross_comm_details_display | tojson | forceescape }}'>
                               {{ p.gross_comm_display }}
                                <i class="fas fa-search-plus small ms-1 text-muted"></i>
                            </td>
                            <td class="val-pos " style="cursor:pointer" data-name="{{ p.name }}" data-reversed-comm-details='{{ p.sales_comm_reversed_details | tojson | forceescape }}'>
                               {{ p.sales_rep_comm_reversed_display }}
                                <i class="fas fa-search-plus small ms-1 text-muted"></i>
                            </td>
                            <td class="drilldown-cell val-pos " style="cursor:pointer" data-name="{{ p.name }}" data-label="مكافآت مستحقة" data-details="{{ p.admin_bonus_earned_details | tojson | forceescape }}">
                               {{ p.admin_bonus_earned }}
                                <i class="fas fa-search-plus small ms-1 text-muted"></i>
                            </td>
                            <td class="drilldown-cell val-pos " style="cursor:pointer" data-name="{{ p.name }}" data-label="جزاءات مستردة" data-details="{{ p.admin_penalty_recovered_details | tojson | forceescape }}">
                               {{ p.admin_penalty_recovered }}
                                <i class="fas fa-search-plus small ms-1 text-muted"></i>
                            </td>

                            <!-- Deductions -->
                            <td class="{{ 'val-neg' if p.sales_rep_comm < 0 else 'val-neutral' }}" style="cursor:pointer; text-decoration:underline" data-name="{{ p.name }}" data-sales-comm-details='{{ p.sales_comm_details | tojson | forceescape }}'>
                               {{ p.sales_rep_comm }}
                                <i class="fas fa-search-plus small ms-1 text-muted"></i>
                            </td>
                            <td class="{{ 'val-neg' if p.returns < 0 else 'val-neutral' }}" style="cursor:pointer" data-name="{{ p.name }}" data-returns-details='{{ p.returns_details | tojson | forceescape }}'>
                               {{ p.returns }}
                                <i class="fas fa-search-plus small ms-1 text-muted"></i>
                            </td>
                            <td class="drilldown-cell {{ 'val-neg' if p.admin_penalty_deducted < 0 else 'val-neutral' }}" style="cursor:pointer" data-name="{{ p.name }}" data-label="جزاءات عالشريك" data-details="{{ p.admin_penalty_deducted_details | tojson | forceescape }}">
                               {{ p.admin_penalty_deducted }}
                                <i class="fas fa-search-plus small ms-1 text-muted"></i>
                            </td>
                            <td class="drilldown-cell {{ 'val-neg' if p.admin_bonus_paid < 0 else 'val-neutral' }}" style="cursor:pointer" data-name="{{ p.name }}" data-label="مكافآت مدفوعة" data-details="{{ p.admin_bonus_paid_details | tojson | forceescape }}">
                               {{ p.admin_bonus_paid }}
                                <i class="fas fa-search-plus small ms-1 text-muted"></i>
                            </td>
                            <td class="drilldown-cell {{ 'val-neg' if p.discounts < 0 else 'val-neutral' }}" style="cursor:pointer" data-name="{{ p.name }}" data-label="خصم فواتير" data-details="{{ p.discounts_details | tojson | forceescape }}">
                               {{ p.discounts }}
                                <i class="fas fa-search-plus small ms-1 text-muted"></i>
                            </td>
                            <td class="drilldown-cell {{ 'val-neg' if p.staff_costs < 0 else 'val-neutral' }}" style="cursor:pointer" data-name="{{ p.name }}" data-label="مصاريف طاقم" data-details="{{ p.staff_costs_details | tojson | forceescape }}">
                               {{ p.staff_costs }}
                                <i class="fas fa-search-plus small ms-1 text-muted"></i>
                            </td>
                            <td class="drilldown-cell {{ 'val-neg' if p.expenses < 0 else 'val-neutral' }}" style="cursor:pointer" data-name="{{ p.name }}" data-label="مصاريف أخرى" data-details="{{ p.expenses_details | tojson | forceescape }}">
                               {{ p.expenses }}
                                <i class="fas fa-search-plus small ms-1 text-muted"></i>
                            </td>

                            <!-- Withdrawals -->
                            <td class="text-danger fw-bold bg-light drilldown-cell" style="cursor:pointer" data-name="{{ p.name }}" data-label="المسحوبات" data-details="{{ p.withdrawals_details | tojson | forceescape }}">
                               {{ p.withdrawals_period }}
                                <i class="fas fa-search-plus small ms-1 text-muted"></i>
                            </td>

                            <!-- Net -->
                            <td class="{{ 'profit-positive' if p.period_net_cash >= 0 else 'profit-negative' }} drilldown-formula" style="cursor:pointer" data-name="{{ p.name }}" data-label="صافي الربح المستحق (المعادلة)" data-formula='[{"label":"عمولة الشريك (+13ج)","value":{{ p.gross_comm_display }}},{"label":"عمولات مستردة","value":{{ p.sales_rep_comm_reversed_display }}},{"label":"مكافآت مستحقة","value":{{ p.admin_bonus_earned }}},{"label":"جزاءات مستردة","value":{{ p.admin_penalty_recovered }}},{"label":"عمولات مبيعات","value":{{ p.sales_rep_comm }}},{"label":"مرتجعات","value":{{ p.returns }}},{"label":"جزاءات عالشريك","value":{{ p.admin_penalty_deducted }}},{"label":"مكافآت مدفوعة","value":{{ p.admin_bonus_paid }}},{"label":"خصم فواتير","value":{{ p.discounts }}},{"label":"مصاريف طاقم","value":{{ p.staff_costs }}},{"label":"مصاريف أخرى","value":{{ p.expenses }}},{"label":"المسحوبات","value":-{{ p.withdrawals_period }}}]' data-result="{{ p.period_net_cash }}">
                               {{ p.period_net_cash }} ج.م
                                <i class="fas fa-calculator small ms-1 text-muted"></i>
                            </td>
                        </tr>
                        {% endfor %}
                        {% endfor %}
                    </tbody>
"""

new_content = content[:start_idx] + tbody_content + content[end_idx:]

with open(path, 'w', encoding='utf-8') as f:
    f.write(new_content)

print("Updated partners_report.html table")
