import os
from sqlalchemy import create_engine, MetaData, text
from dotenv import dotenv_values
import sys

# Load env variables (now pointing to the new Postgres DB)
env_vars = dotenv_values(".env")
pg_url = env_vars.get("DATABASE_URL")

print(f"Connecting to Postgres (New Supabase): {pg_url}")
pg_engine = create_engine(pg_url)
sqlite_engine = create_engine('sqlite:///erp_crm.db')

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# First, create tables in the new Postgres database if they don't exist
try:
    from backend.core import db, app
    with app.app_context():
        # app.config uses Postgres URL since it's loaded from env and not overridden
        db.create_all()
        print("Postgres tables created/verified.")
except Exception as e:
    print(f"Could not auto-create tables via app: {e}")

# Reflect schemas
pg_meta = MetaData()
pg_meta.reflect(bind=pg_engine)

sqlite_meta = MetaData()
sqlite_meta.reflect(bind=sqlite_engine)

with pg_engine.begin() as pg_conn:
    # Disable foreign key checks for this session
    pg_conn.execute(text("SET session_replication_role = 'replica';"))
    
    # We will truncate all tables first to ensure it's empty
    for table in reversed(pg_meta.sorted_tables):
        print(f"Truncating Postgres table {table.name}...")
        pg_conn.execute(text(f'TRUNCATE TABLE "{table.name}" CASCADE;'))
        
    for table in sqlite_meta.sorted_tables:
        print(f"Migrating table {table.name} from SQLite to Postgres...")
        
        if table.name not in pg_meta.tables:
            print(f"  -> Table {table.name} not found in Postgres. Skipping.")
            continue
            
        pg_table = pg_meta.tables[table.name]
        
        with sqlite_engine.connect() as sqlite_conn:
            rows = sqlite_conn.execute(table.select()).fetchall()
            if not rows:
                print("  -> No data in SQLite, skipping.")
                continue
            
            sqlite_cols = set(c.name for c in table.columns)
            pg_cols = set(c.name for c in pg_table.columns)
            common_cols = sqlite_cols.intersection(pg_cols)
            
            insert_data = []
            for row in rows:
                row_dict = {}
                for col in common_cols:
                    row_dict[col] = row._mapping[col]
                insert_data.append(row_dict)
            
            # Insert in batches
            for i in range(0, len(insert_data), 1000):
                pg_conn.execute(pg_table.insert(), insert_data[i:i+1000])
                
            print(f"  -> Inserted {len(insert_data)} rows into {table.name}.")
            
            # Update sequences for auto-increment PKs in Postgres
            try:
                pg_conn.execute(text(f"SELECT setval('{table.name}_id_seq', COALESCE((SELECT MAX(id)+1 FROM \"{table.name}\"), 1), false);"))
                print(f"  -> Sequence updated for {table.name}.")
            except Exception as e:
                pass # Not all tables have id_seq

print("Data pushed successfully from SQLite to Postgres (Supabase)!")
