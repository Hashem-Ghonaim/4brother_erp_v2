import os
import glob
import re

for file in glob.glob('backend/routes/*.py'):
    with open(file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Check if to_char is used for YYYY-MM-DD
    if "func.to_char(" in content and "'YYYY-MM-DD')" in content:
        # replace func.to_char(EXPR, 'YYYY-MM-DD') with cast(EXPR, Date)
        new_content = re.sub(r"func\.to_char\((.*?),\s*'YYYY-MM-DD'\)", r'cast(\1, Date)', content)
        
        # Make sure cast and Date are imported
        if 'from sqlalchemy import' in new_content:
            if 'cast' not in new_content or 'Date' not in new_content:
                new_content = new_content.replace('from sqlalchemy import ', 'from sqlalchemy import cast, Date, ')
            
        with open(file, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f'Updated {file}')
