with open('python_scripts/provider_catalog.py', 'r', encoding='utf-8') as f:
    for i, line in enumerate(f, 1):
        if 'longcat' in line.lower():
            print(f"{i}: {line.strip()}")
