import os
import glob
import re

pattern = re.compile(r"func\.strftime\('%Y-%m',\s*([^)]+)\)")

for file in glob.glob('backend/routes/*.py'):
    with open(file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    new_content = pattern.sub(r"func.to_char(\1, 'YYYY-MM')", content)
    
    if new_content != content:
        with open(file, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f'Updated {file}')
