import os
from sqlalchemy import create_engine, MetaData, text
from dotenv import dotenv_values
import sys

# Load env without modifying os.environ so we can use the old url
env_vars = dotenv_values(".env")
pg_url = env_vars.get("DATABASE_URL")

if not pg_url or pg_url.startswith("#"):
    # If not found or commented, let's just hardcode the known one
    pg_url = "postgresql://postgres.ezyljgnbukgdkhtzqdqo:Mostafa%23%24Hashem2026%40%40@aws-0-eu-west-1.pooler.supabase.com:6543/postgres?sslmode=require"

print(f"Connecting to Postgres: {pg_url}")
pg_engine = create_engine(pg_url)
sqlite_engine = create_engine('sqlite:///erp_crm.db')

# To make sure tables exist in SQLite, we can import the app and create_all
# But first we need to make sure the app uses SQLite.
os.environ['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///erp_crm.db'

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from backend.core import db, app
    with app.app_context():
        # Override to use SQLite for this context just in case
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(app.root_path, '..', 'erp_crm.db')
        db.create_all()
        print("SQLite tables created/verified.")
except Exception as e:
    print(f"Could not auto-create tables via app (maybe already exist): {e}")


pg_meta = MetaData()
pg_meta.reflect(bind=pg_engine)

sqlite_meta = MetaData()
sqlite_meta.reflect(bind=sqlite_engine)

with sqlite_engine.begin() as sqlite_conn:
    # Disable foreign key checks in SQLite during mass insert
    sqlite_conn.execute(text("PRAGMA foreign_keys = OFF;"))
    
    for table in pg_meta.sorted_tables:
        print(f"Migrating table {table.name}...")
        
        if table.name not in sqlite_meta.tables:
            print(f"  -> Table {table.name} not found in SQLite. Skipping.")
            continue
            
        sqlite_table = sqlite_meta.tables[table.name]
        
        # Clear existing data
        sqlite_conn.execute(sqlite_table.delete())
        
        with pg_engine.connect() as pg_conn:
            rows = pg_conn.execute(table.select()).fetchall()
            if not rows:
                print("  -> No data in PG, skipping.")
                continue
            
            pg_cols = set(c.name for c in table.columns)
            sqlite_cols = set(c.name for c in sqlite_table.columns)
            common_cols = pg_cols.intersection(sqlite_cols)
            
            insert_data = []
            for row in rows:
                row_dict = {}
                for col in common_cols:
                    row_dict[col] = row._mapping[col]
                insert_data.append(row_dict)
            
            for i in range(0, len(insert_data), 1000):
                sqlite_conn.execute(sqlite_table.insert(), insert_data[i:i+1000])
                
            print(f"  -> Inserted {len(insert_data)} rows into {table.name}.")

print("Data pulled successfully from Supabase to SQLite!")
