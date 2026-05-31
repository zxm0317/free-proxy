with open('python_scripts/web/index.html', 'r', encoding='utf-8') as f:
    html = f.read()

ids_to_check = [
    'toast', 'providerGrid', 'providerTabs', 'modelList', 'modelSearch', 
    'modelSummary', 'diagnosticsBox', 'chatProvider', 'chatModel', 
    'chatPrompt', 'modelSuggestions', 'openClawStatus', 'openClawActions', 'backupSection'
]

for d_id in ids_to_check:
    present = f'id="{d_id}"' in html or f"id='{d_id}'" in html
    print(f'{d_id}: {"PRESENT" if present else "MISSING!"}')
