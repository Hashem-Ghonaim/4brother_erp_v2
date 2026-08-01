import os
import re

template_dir = r"d:\Work\احمد عبدالفتاح\4brother\templates"

# This regex matches /static/uploads/{{ expression }}
pattern = re.compile(r'/static/uploads/\{\{\s*(.+?)\s*\}\}')

for root, _, files in os.walk(template_dir):
    for file in files:
        if file.endswith(".html"):
            filepath = os.path.join(root, file)
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            new_content = pattern.sub(r'{{ \1 | image_url }}', content)
            
            if new_content != content:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                print(f"Updated {file}")
