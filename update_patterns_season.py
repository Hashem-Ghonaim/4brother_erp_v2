import sqlite3

def run():
    conn = sqlite3.connect('erp_crm.db')
    cursor = conn.cursor()
    
    # Add column if not exists
    try:
        cursor.execute("ALTER TABLE pattern_tracking ADD COLUMN season VARCHAR(50) DEFAULT 'صيفي 2026'")
    except sqlite3.OperationalError:
        print("Column might already exist")
        pass
        
    # Update default values
    cursor.execute("UPDATE pattern_tracking SET season = 'صيفي 2026' WHERE season IS NULL")
    
    # Update specific winter items
    cursor.execute("UPDATE pattern_tracking SET season = 'شتوي 2027' WHERE serial_number LIKE '%سويتشرت%' OR serial_number LIKE '%شتوي%'")
    
    conn.commit()
    conn.close()
    print("Pattern tracking updated successfully!")

if __name__ == '__main__':
    run()
