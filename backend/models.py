from datetime import datetime, date
from flask_login import UserMixin

from .core import db, cairo_now
class SystemSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(100), default="My ERP")
    company_logo = db.Column(db.String(150), default="default_logo.png") # اسم ملف الصورة
    theme_color = db.Column(db.String(20), default="#0d6efd") # لون النظام

class AttendanceSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    grace_period = db.Column(db.Integer, default=15)  # فترة السماح (دقيقة)
    # شرائح الجزاءات - 4 مستويات
    tier1_max_mins = db.Column(db.Integer, default=30)  # حد شريحة 1
    tier1_penalty = db.Column(db.Float, default=0.25)    # جزاء شريحة 1 (نسبة من اليوم)
    tier2_max_mins = db.Column(db.Integer, default=60)
    tier2_penalty = db.Column(db.Float, default=0.5)
    tier3_max_mins = db.Column(db.Integer, default=120)
    tier3_penalty = db.Column(db.Float, default=1.0)
    tier4_penalty = db.Column(db.Float, default=2.0)     # أكثر من شريحة 3
    # جزاء الغياب
    absent_no_excuse = db.Column(db.Float, default=1.0)  # غياب بدون إذن (نسبة من اليوم)
    absent_excused = db.Column(db.Float, default=0.5)     # غياب بإذن (نسبة من اليوم)
    absent_full_day_excuse = db.Column(db.Float, default=0.0) # إذن يوم كامل
    # عدم تسجيل انصراف
    no_checkout_penalty = db.Column(db.Float, default=2.0) # جزاء عدم تسجيل انصراف (نسبة)
    # أيام الإجازة (تخطيها)
    skip_friday = db.Column(db.Boolean, default=True)
    skip_saturday = db.Column(db.Boolean, default=False)

# === كلاس المستخدم الموحد (ضعه مرة واحدة فقط) ===
# === كلاس المستخدم (النسخة النهائية الكاملة) ===
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    fullname = db.Column(db.String(100), nullable=False)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    phone = db.Column(db.String(20))
    base_salary = db.Column(db.Float, default=0.0)
    job_type = db.Column(db.String(20), default='fixed')
    emp_code = db.Column(db.String(20), unique=True)
    permissions = db.Column(db.Text, default="")
    manager = db.relationship('User', remote_side=[id], backref='subordinates')
    manager_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    shift_start = db.Column(db.String(10), default='13:00')
    shift_end = db.Column(db.String(10), default='17:00')

    # الأعمدة التي كانت ناقصة وسببت المشكلة 👇
    commission_value = db.Column(db.Float, default=0.0) # قيمة العمولة الثابتة
    commission_rules = db.Column(db.Text, nullable=True) # قواعد الشرائح (JSON)
    is_shared_salary = db.Column(db.Boolean, default=False) # هل الموظف مشترك (الراتب مقسم)؟
    has_flexible_hours = db.Column(db.Boolean, default=False) # مواعيد مرنة (لا يخصم تأخير/انصراف مبكر)

    def has_perm(self, perm):
        if self.role == 'general_manager':
            return True
        if not self.permissions:
            return False
        return perm in self.permissions.split(',')
class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    date = db.Column(db.Date, default=date.today)
    check_in = db.Column(db.DateTime, nullable=True)
    check_out = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default='present')
    user = db.relationship('User', backref='attendance_records')
# في ملف app.py - داخل كلاس User

class PatternTracking(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    serial_number = db.Column(db.String(50), nullable=False)
    image = db.Column(db.String(255), nullable=True) # مسار الصورة
    entry_date = db.Column(db.Date, default=date.today)
    delivery_date = db.Column(db.Date, nullable=True)
    factory_name = db.Column(db.String(100), nullable=False)

    # علاقة العميل (لكل موظف عملاؤه، والمالك كل العملاء)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    customer = db.relationship('Customer', backref='patterns')

    # علاقة المسؤول (قائمة بكل الموظفين)
    responsible_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    responsible = db.relationship('User', foreign_keys=[responsible_id])

    cost = db.Column(db.Float, default=0.0)
    receiving_price = db.Column(db.Float, default=0.0)
    selling_price = db.Column(db.Float, default=0.0)
    quantity = db.Column(db.Integer, default=0)
    colors = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(50), default='جاري التجهيز') # تم التسليم للمصنع - جاري التجهيز - معطلة - ملغية - تم الإستلام


class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)

class ProductModel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'))
    image = db.Column(db.String(150), default='default.png')
    season = db.Column(db.String(20), default='صيفي')
    category = db.relationship('Category', backref='products')
    variants = db.relationship('ProductVariant', backref='model', lazy=True, cascade="all, delete-orphan")
class EmployeeExcuse(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    date = db.Column(db.Date, default=date.today)
    type = db.Column(db.String(20)) # 'day' أو 'hours'
    hours = db.Column(db.Float, default=0.0)
    note = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=cairo_now)

    user = db.relationship('User', backref='excuses')
class ProductVariant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    model_id = db.Column(db.Integer, db.ForeignKey('product_model.id'))
    barcode = db.Column(db.String(50), unique=True)
    cost_price = db.Column(db.Float)
    sell_price = db.Column(db.Float)
    stock = db.Column(db.Integer, default=0)

class Supplier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20))
    balance = db.Column(db.Float, default=0.0)
# جدول جديد لتسجيل حركات الشركاء بالتفصيل الممل
class PartnerTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    partner_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    order_id = db.Column(db.Integer, db.ForeignKey('sale_order.id'), nullable=True)
    type = db.Column(db.String(50))
    amount = db.Column(db.Float)
    description = db.Column(db.String(255))
    date = db.Column(db.DateTime, default=cairo_now)

    # العلاقات
    # لاحظ: نحدد foreign_keys هنا لأن الجدول فيه علاقة مع User
    partner = db.relationship('User', foreign_keys=[partner_id], backref='transactions')
    order = db.relationship('SaleOrder')
class SupplierPayment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey('supplier.id'))
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.DateTime, default=cairo_now)
    receipt_image = db.Column(db.String(150))
    notes = db.Column(db.String(200))

    # === الإضافة الجديدة: ربط الدفع بالخزينة ===
    account_id = db.Column(db.Integer, db.ForeignKey('money_account.id'))

    supplier = db.relationship('Supplier', backref='payments')
    account = db.relationship('MoneyAccount') # عشان نقدر نجيب اسم الخزنة

class PurchaseOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey('supplier.id'))
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    date = db.Column(db.DateTime, default=cairo_now)
    total_cost = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(20), default='received')
    supplier = db.relationship('Supplier', backref='orders')
    items = db.relationship('PurchaseItem', backref='purchase_order', lazy=True, cascade="all, delete-orphan")

class PurchaseItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    purchase_id = db.Column(db.Integer, db.ForeignKey('purchase_order.id'))
    variant_id = db.Column(db.Integer, db.ForeignKey('product_variant.id'))
    quantity = db.Column(db.Integer)
    unit_cost = db.Column(db.Float)
    total_cost = db.Column(db.Float)
    variant = db.relationship('ProductVariant')

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), unique=True, nullable=False)
    address = db.Column(db.String(200))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=cairo_now)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_by = db.relationship('User', foreign_keys=[created_by_id])
    orders = db.relationship('SaleOrder', backref='customer', lazy=True)
    balance = db.Column(db.Float, default=0.0) # رصيد العميل (مديونيته)
class CustomerPayment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'))
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.DateTime, default=cairo_now)
    account_id = db.Column(db.Integer, db.ForeignKey('money_account.id'))
    notes = db.Column(db.String(200))

    customer = db.relationship('Customer', backref='payments_received')
    account = db.relationship('MoneyAccount')
class ShippingCompany(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20))
    cs_number = db.Column(db.String(20))
    fee_first_1k = db.Column(db.Float, default=0.0)
    fee_extra_1k = db.Column(db.Float, default=0.0)
    orders = db.relationship('SaleOrder', backref='courier', lazy=True)

class SaleOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=True)
    date = db.Column(db.DateTime, default=cairo_now)
    total_amount = db.Column(db.Float)
    discount = db.Column(db.Float, default=0.0)
    final_total = db.Column(db.Float)
    sales_rep_code = db.Column(db.String(50))
    packer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    packer = db.relationship('User', foreign_keys=[packer_id])
    is_shipping = db.Column(db.Boolean, default=False)
    shipping_company_id = db.Column(db.Integer, db.ForeignKey('shipping_company.id'), nullable=True)
    shipping_fee = db.Column(db.Float, default=0.0)
    paid_upfront = db.Column(db.Float, default=0.0)
    amount_due = db.Column(db.Float, default=0.0)
    waybill_no = db.Column(db.String(50), nullable=True)
    shipping_status = db.Column(db.String(20), default='none')
    shipping_settled_date = db.Column(db.DateTime, nullable=True) # تاريخ التحصيل الفعلي
    is_proforma = db.Column(db.Boolean, default=False)
    shipping_notes = db.Column(db.String(255), nullable=True)
    items = db.relationship('SaleItem', backref='order', lazy=True, cascade="all, delete-orphan")
    sales_rep = db.relationship('User', backref='sales', foreign_keys=[user_id])
    returns = db.relationship('ReturnInvoice', backref=db.backref('original_order', overlaps="return_invoice,order"), lazy=True, overlaps="order,return_invoice")

    @property
    def return_details(self):
        # We check both the invoice relationship AND the shipping status for old data
        if not self.returns and self.shipping_status not in ['returned', 'partial_return']:
            return None

        original_qty = sum(item.quantity for item in self.items)
        returned_qty = sum((ret.total_qty or 0) for ret in self.returns)

        # نجيب تفاصيل الأصناف المرتجعة من حركات المخزون (بالبحث المرن لدعم القديم)
        movements = StockMovement.query.filter(StockMovement.reason.like(f"%مرتجع فاتورة #{self.id}%")).all()

        # الفواتير القديمة مكنش بيتسجل فيها إجمالي القطع في الفاتورة المرتجعة، فهنحسبها من المخزون
        if returned_qty == 0 and movements:
            returned_qty = sum(mv.quantity_change for mv in movements)

        if returned_qty == 0:
            # مفيش تفاصيل في المرتجعات ولا حركات المخزون المربوطة
            is_partial = (self.shipping_status == 'partial_return')

            # تصحيح لحالة المرتجعات القديمة اللي اتعلمت بالخطأ كـ جزئي عشان ملهاش سجل في return_invoices
            if is_partial and self.amount_due <= 0 and not self.returns and not movements:
                is_partial = False

            type_str = 'مرتجع جزئي' if is_partial else 'مرتجع كلي (قديم)'
            return {
                'type': type_str,
                'details': 'تفاصيل القطع غير مسجلة في الفواتير القديمة',
                'returned_qty': 0,
                'is_partial': is_partial
            }

        details_list = []
        for mv in movements:
            if mv.variant and mv.variant.model:
                details_list.append(f"{mv.quantity_change}x {mv.variant.model.name}")

        type_str = 'مرتجع كلي' if returned_qty >= original_qty else 'مرتجع جزئي'
        details_str = '، '.join(details_list) if details_list else f"{returned_qty} قطع غير معروفة"

        return {
            'type': type_str,
            'details': details_str,
            'returned_qty': returned_qty,
            'is_partial': (returned_qty < original_qty)
        }

class SaleItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('sale_order.id'))
    variant_id = db.Column(db.Integer, db.ForeignKey('product_variant.id'))
    quantity = db.Column(db.Integer)
    unit_price = db.Column(db.Float)
    total_price = db.Column(db.Float)
    variant = db.relationship('ProductVariant')
class FinancialTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(50), nullable=False)
    category = db.Column(db.String(100))
    amount = db.Column(db.Float, nullable=False)
    description = db.Column(db.Text)
    date = db.Column(db.DateTime, default=datetime.now)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))

    # === الإضافة الجديدة 👇 ===
    account_id = db.Column(db.Integer, db.ForeignKey('money_account.id'), nullable=True)

    created_by = db.relationship('User', foreign_keys=[created_by_id])
    account = db.relationship('MoneyAccount', backref='transactions')
class ReturnInvoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('sale_order.id'), nullable=False)
    date = db.Column(db.DateTime, default=datetime.now)
    shipping_loss = db.Column(db.Float, default=0.0)
    missing_items_cost = db.Column(db.Float, default=0.0)
    missing_items_desc = db.Column(db.String(255))
    total_deduction = db.Column(db.Float, default=0.0)
    total_qty = db.Column(db.Integer, default=0) # كمية المرتجعات
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    notes = db.Column(db.Text)
    order = db.relationship('SaleOrder', backref=db.backref('return_invoices', lazy=True, overlaps="returns,original_order"), overlaps="original_order,returns")
    creator = db.relationship('User')
class MoneyAccount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    type = db.Column(db.String(20), default='cash') # cash, vodafone, bank, instapay
    account_number = db.Column(db.String(50)) # رقم الموبايل أو رقم الحساب
    balance = db.Column(db.Float, default=0.0)
class StockMovement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    variant_id = db.Column(db.Integer, db.ForeignKey('product_variant.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    quantity_change = db.Column(db.Integer)
    reason = db.Column(db.String(100))
    timestamp = db.Column(db.DateTime, default=cairo_now)
    variant = db.relationship('ProductVariant', backref='movements')
    user = db.relationship('User')

class HRTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    type = db.Column(db.String(20))
    amount = db.Column(db.Float)
    date = db.Column(db.DateTime, default=cairo_now)
    note = db.Column(db.String(200))

class ExpenseCategory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)

class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey('expense_category.id'))
    amount = db.Column(db.Float, nullable=False)
    description = db.Column(db.String(200))
    date = db.Column(db.DateTime, default=cairo_now)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    is_shared = db.Column(db.Boolean, default=False)

    # === الإضافة الجديدة ===
    account_id = db.Column(db.Integer, db.ForeignKey('money_account.id'))

    # العلاقات
    category = db.relationship('ExpenseCategory', backref='expenses')
    created_by = db.relationship('User', foreign_keys=[user_id])
    account = db.relationship('MoneyAccount') # عشان نعرف اسم الخزنة

