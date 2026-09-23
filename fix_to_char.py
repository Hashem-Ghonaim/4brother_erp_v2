import glob
import re

def process_content(content):
    idx = 0
    while True:
        idx = content.find('func.to_char(', idx)
        if idx == -1:
            break
        
        start = idx + len('func.to_char(')
        parens = 1
        end = start
        while parens > 0 and end < len(content):
            if content[end] == '(': parens += 1
            elif content[end] == ')': parens -= 1
            end += 1
            
        if parens == 0:
            full_expr = content[start:end-1]
            if "'YYYY-MM'" in full_expr or '"YYYY-MM"' in full_expr:
                expr = full_expr.replace(", 'YYYY-MM'", "").replace(', "YYYY-MM"', "").strip()
                replacement = f"func.strftime('%Y-%m', {expr})"
                content = content[:idx] + replacement + content[end:]
                idx += len(replacement)
            elif "'YYYY-MM-DD'" in full_expr or '"YYYY-MM-DD"' in full_expr:
                expr = full_expr.replace(", 'YYYY-MM-DD'", "").replace(', "YYYY-MM-DD"', "").strip()
                # Use func.date for SQLite
                replacement = f"func.date({expr})"
                content = content[:idx] + replacement + content[end:]
                idx += len(replacement)
            else:
                idx += 1
        else:
            idx += 1
            
    # Also handle cast(xxx, Date) if any, though SQLite supports CAST(expr AS DATE) via SQLAlchemy Cast.
    return content

for file in glob.glob('backend/routes/*.py'):
    with open(file, 'r', encoding='utf-8') as f:
        content = f.read()
    new_content = process_content(content)
    if new_content != content:
        with open(file, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f'Updated {file}')
