from flask import render_template, request, redirect, url_for, flash
from flask_login import current_user
from sqlalchemy import or_

from ..core import db, cairo_now
from ..models import MoneyAccount, FinancialTransaction


def register_treasury_routes(app, general_manager_required):
    @app.route('/treasury')
    @general_manager_required
    def treasury_dashboard():
        accounts = MoneyAccount.query.all()

        grouped = {'cash': [], 'vodafone': [], 'bank': [], 'instapay': []}
        totals = {'cash': 0, 'vodafone': 0, 'bank': 0, 'instapay': 0, 'all': 0}

        for acc in accounts:
            acc_type = acc.type if acc.type in grouped else 'cash'
            grouped[acc_type].append(acc)
            totals[acc_type] += acc.balance
            totals['all'] += acc.balance

        return render_template('treasury.html', grouped=grouped, totals=totals)

    @app.route('/treasury/<int:id>')
    @general_manager_required
    def account_details(id):
        account = MoneyAccount.query.get_or_404(id)

        transactions = FinancialTransaction.query.filter(
            or_(
                FinancialTransaction.account_id == id,
                ((FinancialTransaction.account_id == None) &
                 FinancialTransaction.description.like(f"%{account.name}%"))
            )
        ).order_by(FinancialTransaction.date.desc()).all()

        current_run_bal = account.balance
        for tx in transactions:
            actual_change = 0
            if tx.account_id == account.id or tx.account_id is None:
                actual_change = tx.amount

            tx.balance_after = round(current_run_bal, 2)
            tx.balance_before = round(current_run_bal - actual_change, 2)
            current_run_bal = tx.balance_before

        return render_template('account_details.html', account=account, transactions=transactions)

    @app.route('/treasury/edit/<int:id>', methods=['POST'])
    @general_manager_required
    def edit_account(id):
        account = MoneyAccount.query.get_or_404(id)
        account.name = request.form['name']
        account.account_number = request.form['account_number']

        db.session.commit()
        flash('تم تعديل بيانات الحساب بنجاح ✅', 'success')
        return redirect(url_for('account_details', id=id))

    @app.route('/treasury/delete/<int:id>')
    @general_manager_required
    def delete_account(id):
        account = MoneyAccount.query.get_or_404(id)
        try:
            db.session.delete(account)
            db.session.commit()
            flash('تم حذف الحساب بنجاح 🗑️', 'warning')
            return redirect(url_for('treasury_dashboard'))
        except Exception:
            db.session.rollback()
            flash('لا يمكن حذف هذا الحساب لوجود عمليات مرتبطة به', 'danger')
            return redirect(url_for('account_details', id=id))

    @app.route('/treasury/add', methods=['POST'])
    @general_manager_required
    def add_account():
        name = request.form['name']
        acc_type = request.form['type']
        number = request.form.get('account_number', '')

        db.session.add(MoneyAccount(name=name, type=acc_type, account_number=number, balance=0.0))
        db.session.commit()
        flash('تم إضافة الحساب بنجاح', 'success')
        return redirect(url_for('treasury_dashboard'))

    @app.route('/treasury/deposit', methods=['POST'])
    @general_manager_required
    def manual_deposit():
        try:
            account_id = request.form.get('account_id')
            amount = float(request.form.get('amount', 0))
            notes = request.form.get('notes', '')

            if amount <= 0:
                flash('المبلغ يجب أن يكون أكبر من صفر', 'danger')
                return redirect(url_for('treasury_dashboard'))

            acc = MoneyAccount.query.get(account_id)
            if not acc:
                flash('الحساب غير موجود', 'danger')
                return redirect(url_for('treasury_dashboard'))

            acc.balance = round(acc.balance + amount, 2)

            db.session.add(FinancialTransaction(
                account_id=acc.id,
                type='deposit',
                category='إيداع يدوي',
                amount=amount,
                description=notes or 'إيداع يدوي من خارج السيستم',
                created_by_id=current_user.id
            ))

            db.session.commit()
            flash(f'تم إيداع {amount} ج.م في {acc.name} بنجاح ✅', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'حدث خطأ: {str(e)}', 'danger')

        return redirect(url_for('treasury_dashboard'))

    @app.route('/treasury/transfer', methods=['POST'])
    @general_manager_required
    def transfer_balance():
        try:
            from_id = request.form.get('from_account')
            to_id = request.form.get('to_account')
            amount = float(request.form.get('amount'))
            notes = request.form.get('notes', '')

            if from_id == to_id:
                flash('لا يمكن التحويل لنفس الحساب', 'warning')
                return redirect(url_for('treasury_dashboard'))

            from_acc = MoneyAccount.query.get(from_id)
            to_acc = MoneyAccount.query.get(to_id)

            if not from_acc or not to_acc:
                flash('حسابات غير صحيحة', 'danger')
                return redirect(url_for('treasury_dashboard'))

            from_acc.balance = round(from_acc.balance - amount, 2)
            to_acc.balance = round(to_acc.balance + amount, 2)

            db.session.add(FinancialTransaction(
                account_id=from_acc.id,
                type='transfer_out',
                category='تحويل داخلي',
                amount=-amount,
                description=f"تحويل صادر إلى {to_acc.name} ({notes})",
                created_by_id=current_user.id,
                date=cairo_now()
            ))

            db.session.add(FinancialTransaction(
                account_id=to_acc.id,
                type='transfer_in',
                category='تحويل داخلي',
                amount=amount,
                description=f"تحويل وارد من {from_acc.name} ({notes})",
                created_by_id=current_user.id,
                date=cairo_now()
            ))

            db.session.commit()
            flash(f'تم تحويل {amount} ج.م من {from_acc.name} إلى {to_acc.name} بنجاح ✅', 'success')

        except Exception as e:
            db.session.rollback()
            flash(f'حدث خطأ: {str(e)}', 'danger')

        return redirect(url_for('treasury_dashboard'))

    @app.route('/treasury/view')
    @general_manager_required
    def treasury_report():
        return redirect(url_for('treasury_dashboard'))
