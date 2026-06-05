import os
import json
import re

log_files = [
    r'C:\Users\Administrator\.gemini\antigravity\brain\302ad5ad-9f40-4168-b751-5d9234b061db\.system_generated\logs\overview.txt',
    r'C:\Users\Administrator\.gemini\antigravity\brain\cd7038d5-b5e4-49de-9e64-a6feec1278a1\.system_generated\logs\overview.txt'
]

found = False

for path in log_files:
    if not os.path.exists(path):
        continue
    print(f"Checking {path}")
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
        # Find occurrences of 'rel', 'spd', 'int' in a JS-like context or search for "最聪明的" in the logs
        for m in re.finditer(r'[\u4e00-\u9fa5\w\s\"\'\:\,\{\}\[\]\-]{1,100}最聪明的[\u4e00-\u9fa5\w\s\"\'\:\,\{\}\[\]\-]{1,100}', content):
            print("MATCH FOR '最聪明的':", m.group(0))
            found = True
        
        # Search for progress-bar or rel / spd inside the html style tags or JS table builder
        for m in re.finditer(r'item\.rel', content):
            print("MATCH FOR 'item.rel':", content[m.start()-100:m.end()+100])
            found = True

if not found:
    print("No matches found in logs.")
