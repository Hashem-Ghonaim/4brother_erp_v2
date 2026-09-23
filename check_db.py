import os, sys
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
from werkzeug.security import generate_password_hash
import psycopg2

conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()

new_hash = generate_password_hash("123456")
cur.execute('UPDATE "user" SET password = %s WHERE username = %s', (new_hash, 'gm_ahmed'))
conn.commit()
print(f"Password reset for gm_ahmed to '123456' - Done!")
conn.close()
