with open('python_scripts/server_fastapi.py', 'r', encoding='utf-8') as f:
    for i, line in enumerate(f, 1):
        if 'provider-keys' in line.lower() or 'preferred-model' in line.lower() or 'manual-order' in line.lower():
            print(f"{i}: {line.strip()}")
