import os
from dotenv import load_dotenv
load_dotenv()
from sqlalchemy import create_engine, text

db_url = os.environ.get('DATABASE_URL').replace("postgres://", "postgresql://", 1)
engine = create_engine(db_url)

with engine.connect() as conn:
    users = conn.execute(text('SELECT id, fullname, role, manager_id, commission_value FROM "user"')).fetchall()
    for u in users:
        print(f"ID: {u[0]:<3} | Name: {u[1]:<20} | Role: {u[2]:<15} | MgrID: {str(u[3]):<4} | Comm: {u[4]}")
