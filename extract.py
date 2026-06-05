import re
html = open('python_scripts/web/index.html', encoding='utf-8').read()
match = re.search(r'<script>(.*?)</script>', html, re.DOTALL)
if match:
    open('test_script.js', 'w', encoding='utf-8').write(match.group(1))
