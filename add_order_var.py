html_path = r'c:\Users\Administrator\free-proxy\python_scripts\web\index.html'

with open(html_path, 'r', encoding='utf-8') as f:
    content = f.read()

target = "        const toast = document.getElementById('toast');"
replacement = "        let currentModelsOrder = [];\n        const toast = document.getElementById('toast');"

if target in content:
    content = content.replace(target, replacement)
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Successfully added currentModelsOrder variable!")
else:
    print("Target not found!")
