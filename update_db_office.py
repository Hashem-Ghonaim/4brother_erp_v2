import os
import sys

# Change to project dir
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import text
from backend.core import app, db

def update_db():
    with app.app_context():
        with db.engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE supplier ADD COLUMN is_office BOOLEAN DEFAULT 0"))
                print("Added is_office to supplier")
            except Exception as e:
                print(f"Error adding to supplier: {e}")
                
            try:
                conn.execute(text("ALTER TABLE purchase_order ADD COLUMN is_office BOOLEAN DEFAULT 0"))
                print("Added is_office to purchase_order")
            except Exception as e:
                print(f"Error adding to purchase_order: {e}")
                
            conn.commit()

if __name__ == '__main__':
    update_db()
