import re

with open('webapp/index.html', 'r', encoding='utf-8') as f:
    code = f.read()

html_ids = set(re.findall(r'id=["\']([a-zA-Z0-9_\-]+)["\']', code))
js_ids = set(re.findall(r'getElementById\(["\']([a-zA-Z0-9_\-]+)["\']\)', code))

# also check dynamic IDs constructed in JS:
# tag-btn-${t}, modal-tag-${t}, nav-btn-${t}, tab-${t}
ALL_TAGS = ['all', 'python', 'bot', 'frontend', 'backend', 'mobile', 'design', '1c', 'gamedev', 'devops', 'marketing', 'analytics', 'copy']
for t in ALL_TAGS:
    js_ids.add(f'tag-btn-{t}')
    if t != 'all':
        js_ids.add(f'modal-tag-{t}')

for t in ['feed', 'analytics', 'filters', 'premium', 'profile']:
    js_ids.add(f'tab-{t}')
    js_ids.add(f'nav-btn-{t}')

missing = js_ids - html_ids
print("Total HTML IDs:", len(html_ids))
print("Total JS required IDs:", len(js_ids))
if missing:
    print("Missing IDs:", missing)
else:
    print("ALL required IDs are present in HTML!")
