import os
import sys

# Setup environment to load the app
from backend.core import app, db
from backend.models import User, PartnerGroup
from sqlalchemy import text

def migrate_db():
    with app.app_context():
        print("Creating PartnerGroup table if not exists...")
        # Since we just added the model, we need to alter the User table and create PartnerGroup
        
        # 1. Create PartnerGroup table
        db.session.execute(text('''
            CREATE TABLE IF NOT EXISTS partner_group (
                id SERIAL PRIMARY KEY,
                name VARCHAR(50) NOT NULL
            );
        '''))
        
        # 2. Alter User table to add partner_group_id
        try:
            db.session.execute(text('ALTER TABLE "user" ADD COLUMN partner_group_id INTEGER REFERENCES partner_group (id);'))
            print("Added partner_group_id to user table.")
        except Exception as e:
            if 'duplicate column' in str(e).lower() or 'already exists' in str(e).lower():
                print("Column partner_group_id already exists.")
            else:
                print(f"Error adding column: {e}")

        # 3. Create the Groups
        group1 = PartnerGroup.query.filter_by(name="فريق 1 (أحمد هشام + أحمد وجدي)").first()
        if not group1:
            group1 = PartnerGroup(name="فريق 1 (أحمد هشام + أحمد وجدي)")
            db.session.add(group1)
            
        group2 = PartnerGroup.query.filter_by(name="فريق 2 (أحمد أبو اليزيد + أحمد العجان)").first()
        if not group2:
            group2 = PartnerGroup(name="فريق 2 (أحمد أبو اليزيد + أحمد العجان)")
            db.session.add(group2)
            
        db.session.commit()
        print("Created Groups.")
        
        # 4. Assign Users to Groups based on managers
        # Team 1 Managers: "أحمد هشام", "أحمد وجدي"
        # Team 2 Managers: "أحمد أبو اليزيد", "أحمد العجان"
        
        # We need to find their IDs first.
        team1_managers = User.query.filter(User.fullname.in_(["أحمد هشام", "أحمد وجدي"])).all()
        team2_managers = User.query.filter(User.fullname.in_(["أحمد أبو اليزيد", "أحمد العجان"])).all()
        
        for tm in team1_managers:
            tm.partner_group_id = group1.id
            # assign subordinates
            for sub in tm.subordinates:
                sub.partner_group_id = group1.id
                
        for tm in team2_managers:
            tm.partner_group_id = group2.id
            # assign subordinates
            for sub in tm.subordinates:
                sub.partner_group_id = group2.id
                
        db.session.commit()
        print("Assigned users to groups.")
        
        # 5. Set all is_shared_salary to False
        users_updated = User.query.filter_by(is_shared_salary=True).update({'is_shared_salary': False})
        db.session.commit()
        print(f"Updated {users_updated} users to is_shared_salary=False.")

if __name__ == '__main__':
    migrate_db()
