import os
import json
import re

brain_dir = r'C:\Users\Administrator\.gemini\antigravity\brain'

found_matches = []

for root, dirs, files in os.walk(brain_dir):
    for file in files:
        if file == 'overview.txt':
            path = os.path.join(root, file)
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    # Let's search for 'item.rel' or 'item.spd' or 'item.int'
                    # We can use regex to find where it is written
                    for m in re.finditer(r'item\.rel|item\.spd|item\.int', content):
                        start = max(0, m.start() - 1000)
                        end = min(len(content), m.end() + 1000)
                        found_matches.append({
                            'path': path,
                            'pos': m.start(),
                            'context': content[start:end]
                        })
                        # Stop after first few matches to prevent too much output
                        if len(found_matches) >= 10:
                            break
            except Exception as e:
                pass
    if len(found_matches) >= 10:
        break

print(f"Found {len(found_matches)} matches in logs.")
for i, m in enumerate(found_matches):
    filename = f"any_match_{i}.txt"
    with open(filename, 'w', encoding='utf-8') as out:
        out.write(f"PATH: {m['path']}\nPOSITION: {m['pos']}\n\nCONTEXT:\n{m['context']}\n")
    print(f"Saved {filename}")
