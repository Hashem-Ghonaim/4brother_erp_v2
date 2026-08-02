import os
import glob
import re

# We want to replace func.date(EXPR) with func.to_char(EXPR, 'YYYY-MM-DD')
# Since EXPR might contain parentheses (like func.coalesce(A, B)), we'll write a small parser

def process_content(content):
    idx = 0
    while True:
        idx = content.find('func.date(', idx)
        if idx == -1:
            break
        
        # find matching closing parenthesis
        start = idx + len('func.date(')
        parens = 1
        end = start
        while parens > 0 and end < len(content):
            if content[end] == '(':
                parens += 1
            elif content[end] == ')':
                parens -= 1
            end += 1
            
        if parens == 0:
            # We found the matching parenthesis
            expr = content[start:end-1]
            replacement = f"func.to_char({expr}, 'YYYY-MM-DD')"
            content = content[:idx] + replacement + content[end:]
            idx += len(replacement)
        else:
            idx += 1
            
    return content

for file in glob.glob('backend/routes/*.py'):
    with open(file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    new_content = process_content(content)
    
    if new_content != content:
        with open(file, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f'Updated {file}')
