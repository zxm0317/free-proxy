import re
import json

with open(r'C:\Users\Administrator\.gemini\antigravity\brain\302ad5ad-9f40-4168-b751-5d9234b061db\.system_generated\logs\overview.txt', 'r', encoding='utf-8') as f:
    text = f.read()

# the format is usually:
# Tool Call: default_api:multi_replace_file_content
# Arguments: {
#   ...
# }
# Return: ...

# We can find blocks starting with 'Tool Call:'
blocks = text.split('Tool Call: default_api:')
edits = []
for block in blocks[1:]:
    if block.startswith('multi_replace_file_content') or block.startswith('replace_file_content'):
        arg_start = block.find('Arguments: {')
        if arg_start == -1: continue
        arg_start += len('Arguments: ')
        # We need to extract the JSON object.
        # Let's use a brace counter to find the end of the JSON object.
        brace_count = 0
        in_string = False
        escape = False
        arg_end = -1
        for i in range(arg_start, len(block)):
            c = block[i]
            if in_string:
                if escape:
                    escape = False
                elif c == '\\':
                    escape = True
                elif c == '"':
                    in_string = False
            else:
                if c == '"':
                    in_string = True
                elif c == '{':
                    brace_count += 1
                elif c == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        arg_end = i + 1
                        break
        if arg_end != -1:
            json_str = block[arg_start:arg_end]
            try:
                data = json.loads(json_str)
                if 'index.html' in data.get('TargetFile', ''):
                    edits.append(data)
            except Exception as e:
                print("Failed to parse JSON:", str(e)[:100])

print(f"Found {len(edits)} edits for index.html")
if edits:
    print("First edit chunks:", len(edits[0].get('ReplacementChunks', [])))
