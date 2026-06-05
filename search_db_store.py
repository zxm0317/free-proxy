with open('python_scripts/db_store.py', 'r', encoding='utf-8') as f:
    for i, line in enumerate(f, 1):
        if 'create table' in line.lower():
            print(f"{i}: {line.strip()}")
