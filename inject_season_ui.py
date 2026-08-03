import os
import glob

html_files = glob.glob('templates/*.html')

injection = """
<!-- GLOBAL SEASON SELECTOR -->
<div style="position: fixed; bottom: 20px; left: 20px; z-index: 9999; background: white; padding: 10px; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); border: 2px solid var(--primary, #0d6efd);">
    <form action="/set_season_post" method="POST" id="global-season-form" style="margin: 0;">
        <label for="global_season_select" class="fw-bold mb-1 d-block text-primary" style="font-size: 0.85rem;"><i class="fas fa-calendar-alt me-1"></i> الموسم الحالي</label>
        <select name="season" id="global_season_select" class="form-select form-select-sm" onchange="document.getElementById('global-season-form').submit();" style="min-width: 120px; font-weight: bold; cursor: pointer;">
            <option value="صيفي 2026" {% if active_season == 'صيفي 2026' %}selected{% endif %}>☀️ صيفي 2026</option>
            <option value="شتوي 2027" {% if active_season == 'شتوي 2027' %}selected{% endif %}>❄️ شتوي 2027</option>
        </select>
    </form>
</div>
<!-- END GLOBAL SEASON SELECTOR -->
"""

for filepath in html_files:
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Skip if already injected
    if "GLOBAL SEASON SELECTOR" in content:
        continue

    # Find the closing body tag or any safe place to inject
    if '</body>' in content:
        content = content.replace('</body>', injection + '\n</body>')
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Injected into {filepath}")
    else:
        print(f"No </body> found in {filepath}")
