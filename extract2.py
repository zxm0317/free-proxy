import re

with open(r'C:\Users\Administrator\.gemini\antigravity\brain\302ad5ad-9f40-4168-b751-5d9234b061db\.system_generated\logs\overview.txt', 'r', encoding='utf-8') as f:
    text = f.read()

# Look for view_file output.
matches = re.finditer(r'The following code has been modified to include a line number before every line(.*?)The above content does NOT show', text, re.DOTALL)
for i, m in enumerate(matches):
    print(f"View block {i}: {len(m.group(1))} chars")

# Also look for the final view
matches = re.finditer(r'The following code has been modified to include a line number before every line(.*?)^\s*$', text, re.DOTALL | re.MULTILINE)
print("Total view blocks found:", len(list(matches)))

