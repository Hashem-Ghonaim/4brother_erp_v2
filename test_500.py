import os
os.environ['DATABASE_URL'] = 'postgresql://postgres.ezyljgnbukgdkhtzqdqo:Mostafa%23%24Hashem2026%40%40@aws-0-eu-west-1.pooler.supabase.com:6543/postgres?sslmode=require'
from api.index import app
from backend.models import User
import traceback

with app.test_client() as c:
    with app.app_context():
        user = User.query.filter_by(username='gm_ahmed').first()
        
    if user:
        with c.session_transaction() as sess:
            sess['_user_id'] = str(user.id)
            sess['_fresh'] = True
            
    print("Testing /dashboard")
    try:
        r = c.get('/dashboard', follow_redirects=True)
        print("Status:", r.status_code)
        if r.status_code >= 400:
            print("ERROR on /dashboard")
            print(r.data.decode('utf-8')[:500])
    except Exception as e:
        print("EXCEPTION on /dashboard:")
        traceback.print_exc()

    print("Testing /inventory/products")
    try:
        r = c.get('/inventory/products', follow_redirects=True)
        print("Status:", r.status_code)
        if r.status_code >= 400:
            print("ERROR on /inventory/products")
            print(r.data.decode('utf-8')[:500])
    except Exception as e:
        print("EXCEPTION on /inventory/products:")
        traceback.print_exc()
        
    print("Testing /")
    try:
        r = c.get('/', follow_redirects=True)
        print("Status:", r.status_code)
        if r.status_code >= 400:
            print("ERROR on /")
            print(r.data.decode('utf-8')[:500])
    except Exception as e:
        print("EXCEPTION on /")
        traceback.print_exc()
