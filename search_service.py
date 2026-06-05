with open('python_scripts/service.py', 'r', encoding='utf-8') as f:
    for i, line in enumerate(f, 1):
        if 'def configure_provider_key' in line or 'def get_configured_providers' in line:
            print(f"{i}: {line.strip()}")
