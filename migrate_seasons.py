import sqlite3
import os

db_path = 'erp_crm.db'

def run_migration():
    if not os.path.exists(db_path):
        print(f"Database {db_path} not found!")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 1. Reset customer and supplier balances
    print("Resetting Customer and Supplier balances to 0...")
    cursor.execute("UPDATE customer SET balance = 0.0")
    cursor.execute("UPDATE supplier SET balance = 0.0")

    # 2. Add season column to tables
    tables_to_update = [
        'expense',
        'purchase_order',
        'supplier_payment',
        'customer_payment',
        'sale_order',
        'return_invoice',
        'financial_transaction',
        'partner_transaction'
    ]

    for table in tables_to_update:
        print(f"Checking table {table}...")
        try:
            # Add column (if it fails, it means it already exists)
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN season VARCHAR(50) DEFAULT 'صيفي 2026'")
            print(f"Added 'season' column to {table}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                print(f"Column 'season' already exists in {table}")
            else:
                print(f"Error updating {table}: {e}")
                
        # Also, update existing rows to 'صيفي 2026' explicitly just in case SQLite default didn't retroactively apply
        cursor.execute(f"UPDATE {table} SET season = 'صيفي 2026' WHERE season IS NULL")

    conn.commit()
    conn.close()
    print("Migration complete!")

if __name__ == '__main__':
    run_migration()
