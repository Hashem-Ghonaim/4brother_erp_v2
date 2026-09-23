import glob
import re

def reverse_process_content(content):
    # Match func.strftime('%Y-%m', expr) -> func.to_char(expr, 'YYYY-MM')
    pattern_month = re.compile(r"func\.strftime\('%Y-%m',\s*([^)]+)\)")
    content = pattern_month.sub(r"func.to_char(\1, 'YYYY-MM')", content)
    
    # Match func.date(expr) -> func.to_char(expr, 'YYYY-MM-DD')
    pattern_date = re.compile(r"func\.date\(([^)]+)\)")
    content = pattern_date.sub(r"func.to_char(\1, 'YYYY-MM-DD')", content)
    
    return content

for file in glob.glob('backend/routes/*.py'):
    with open(file, 'r', encoding='utf-8') as f:
        content = f.read()
    new_content = reverse_process_content(content)
    if new_content != content:
        with open(file, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f'Reverted {file}')
