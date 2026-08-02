import os
from sqlalchemy import create_engine, MetaData, text

sqlite_engine = create_engine('sqlite:///erp_crm.db')
pg_engine = create_engine('postgresql://postgres.ezyljgnbukgdkhtzqdqo:Mostafa%23%24Hashem2026%40%40@aws-0-eu-west-1.pooler.supabase.com:6543/postgres?sslmode=require')

sqlite_meta = MetaData()
sqlite_meta.reflect(bind=sqlite_engine)

pg_meta = MetaData()
pg_meta.reflect(bind=pg_engine)

with pg_engine.begin() as pg_conn:
    print("Setting replica role to bypass foreign key checks...")
    pg_conn.execute(text("SET session_replication_role = 'replica';"))
    
    print("Altering columns to prevent truncation...")
    pg_conn.execute(text("ALTER TABLE customer ALTER COLUMN phone TYPE VARCHAR(100);"))
    pg_conn.execute(text("ALTER TABLE supplier ALTER COLUMN phone TYPE VARCHAR(100);"))
    pg_conn.execute(text("ALTER TABLE \"user\" ALTER COLUMN phone TYPE VARCHAR(100);"))
    
    for table in sqlite_meta.sorted_tables:
        print(f"Migrating table {table.name}...")
        
        # Check if table exists in PG
        if table.name not in pg_meta.tables:
            print(f"Table {table.name} not found in PostgreSQL. Skipping.")
            continue
            
        pg_table = pg_meta.tables[table.name]
        
        # Clear existing data in PG
        print(f"Clearing existing data in {table.name}...")
        pg_conn.execute(pg_table.delete())
        
        with sqlite_engine.connect() as sqlite_conn:
            rows = sqlite_conn.execute(table.select()).fetchall()
            if not rows:
                print("No data, skipping.")
                continue
            
            sqlite_cols = set(c.name for c in table.columns)
            pg_cols = set(c.name for c in pg_table.columns)
            common_cols = sqlite_cols.intersection(pg_cols)
            
            insert_data = []
            for row in rows:
                row_dict = {}
                for col in common_cols:
                    val = row._mapping[col]
                    if val == "":
                        val = None
                    
                    col_type = str(pg_table.columns[col].type).upper()
                    if isinstance(val, str) and any(t in col_type for t in ('FLOAT', 'NUMERIC', 'DOUBLE', 'REAL', 'INT', 'BIGINT', 'SMALLINT', 'DECIMAL')):
                        val = val.replace(',', '')
                        if val == '':
                            val = None
                            
                    row_dict[col] = val
                insert_data.append(row_dict)
            
            # Batch insert
            for i in range(0, len(insert_data), 1000):
                pg_conn.execute(pg_table.insert(), insert_data[i:i+1000])
                
            print(f"Inserted {len(insert_data)} rows into {table.name}.")

    print("Migration of data complete!")

with pg_engine.begin() as pg_conn:
    # Reset sequences for serial columns
    print("Resetting sequences...")
    for table in pg_meta.sorted_tables:
        tname = f'"{table.name}"' if table.name == 'user' else table.name
        seq_query = text(f"SELECT setval(pg_get_serial_sequence('{tname}', 'id'), COALESCE(MAX(id), 1) + 1, false) FROM {tname};")
        try:
            # We use execution_options(isolation_level="AUTOCOMMIT") to avoid transaction aborts on failure
            with pg_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.execute(seq_query)
        except Exception as e:
            print(f"Skipped sequence reset for {table.name}: {e}")
            
    with pg_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("SET session_replication_role = 'origin';"))
    
    print("Migration fully complete!")
