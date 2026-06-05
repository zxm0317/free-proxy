with open('python_scripts/openai_relay.py', 'r', encoding='utf-8') as f:
    for i, line in enumerate(f, 1):
        if 'siliconflow' in line.lower() or 'deepseek' in line.lower() or 'longcat' in line.lower():
            print(f"{i}: {line.strip()}")
