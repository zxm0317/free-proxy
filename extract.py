import json
import re

with open(r'C:\Users\Administrator\.gemini\antigravity\brain\302ad5ad-9f40-4168-b751-5d9234b061db\.system_generated\logs\overview.txt', 'r', encoding='utf-8') as f:
    text = f.read()

# The tool call JSON might be inside code blocks or specific strings. Let's just find "ReplacementContent"
chunks = re.findall(r'"ReplacementContent":\s*"(.*?)"', text)
print(f'Found {len(chunks)} replacement chunks.')
if chunks:
    print(chunks[-1][:200])
