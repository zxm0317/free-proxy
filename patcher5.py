import json
import re

base_file = r'c:\Users\Administrator\free-proxy\python_scripts\web\index.html'
with open(base_file, 'r', encoding='utf-8') as f:
    content = f.read().replace('\r\n', '\n')

log_files = [
    r'C:\Users\Administrator\.gemini\antigravity\brain\302ad5ad-9f40-4168-b751-5d9234b061db\.system_generated\logs\overview.txt',
    r'C:\Users\Administrator\.gemini\antigravity\brain\cd7038d5-b5e4-49de-9e64-a6feec1278a1\.system_generated\logs\overview.txt'
]

def unescape(s):
    if not isinstance(s, str):
        return s
    # Attempt to decode unicode escapes
    try:
        s = s.encode('utf-8').decode('unicode_escape')
    except:
        pass
    # If it is wrapped in quotes, strip them
    if s.startswith('"') and s.endswith('"'):
        s = s[1:-1]
    return s.replace('\r\n', '\n')

edits_applied = 0

for log_file in log_files:
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            for line in f:
                if not line.strip(): continue
                step = json.loads(line)
                if 'tool_calls' in step:
                    for call in step['tool_calls']:
                        if call['name'] in ['replace_file_content', 'multi_replace_file_content']:
                            args = call['args']
                            target = args.get('TargetFile', '')
                            if 'index.html' not in target:
                                continue
                            
                            chunks = []
                            if call['name'] == 'replace_file_content':
                                chunks = [{
                                    'TargetContent': unescape(args.get('TargetContent', '')),
                                    'ReplacementContent': unescape(args.get('ReplacementContent', ''))
                                }]
                            else:
                                try:
                                    chunks_str = args.get('ReplacementChunks', '[]')
                                    if isinstance(chunks_str, str):
                                        if chunks_str.startswith('"'):
                                            chunks_str = json.loads(chunks_str)
                                    parsed = json.loads(chunks_str)
                                    for c in parsed:
                                        chunks.append({
                                            'TargetContent': unescape(c.get('TargetContent', '')),
                                            'ReplacementContent': unescape(c.get('ReplacementContent', ''))
                                        })
                                except Exception as e:
                                    print("Chunk parsing error:", e)
                            
                            for chunk in chunks:
                                tc = chunk.get('TargetContent', '')
                                rc = chunk.get('ReplacementContent', '')
                                if tc:
                                    if content.count(tc) == 1:
                                        content = content.replace(tc, rc)
                                        edits_applied += 1
                                    elif content.count(tc) > 1:
                                        content = content.replace(tc, rc, 1)
                                        edits_applied += 1
                                    else:
                                        print("Could not find TargetContent! Length:", len(tc), "Sample:", repr(tc[:50]))
    except Exception as e:
        print("Error reading log:", e)

print(f"Total edits applied: {edits_applied}")
with open('python_scripts/web/index.html', 'w', encoding='utf-8') as f:
    f.write(content)
print("Saved to python_scripts/web/index.html")
