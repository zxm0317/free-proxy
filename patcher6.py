import ast
import json

base_file = r'c:\Users\Administrator\free-proxy\python_scripts\web\index.html'
with open(base_file, 'r', encoding='utf-8') as f:
    content = f.read().replace('\r\n', '\n')

log_file = r'C:\Users\Administrator\.gemini\antigravity\brain\302ad5ad-9f40-4168-b751-5d9234b061db\.system_generated\logs\overview.txt'

def smart_unescape(s):
    if not isinstance(s, str):
        return s
    s = s.replace('\r\n', '\n')
    if s.startswith('"') and s.endswith('"'):
        try:
            return ast.literal_eval(s).replace('\r\n', '\n')
        except:
            # Fallback
            return s[1:-1].encode('utf-8').decode('unicode_escape').replace('\r\n', '\n')
    return s

edits_applied = 0
total_edits = 0

with open(log_file, 'r', encoding='utf-8') as f:
    for line in f:
        if not line.strip(): continue
        try:
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
                                'TargetContent': smart_unescape(args.get('TargetContent', '')),
                                'ReplacementContent': smart_unescape(args.get('ReplacementContent', ''))
                            }]
                        else:
                            chunks_str = args.get('ReplacementChunks', '[]')
                            if isinstance(chunks_str, str):
                                if chunks_str.startswith('"'):
                                    try:
                                        chunks_str = ast.literal_eval(chunks_str)
                                    except:
                                        pass
                            try:
                                parsed = json.loads(chunks_str)
                                for c in parsed:
                                    chunks.append({
                                        'TargetContent': smart_unescape(c.get('TargetContent', '')),
                                        'ReplacementContent': smart_unescape(c.get('ReplacementContent', ''))
                                    })
                            except Exception as e:
                                print("Chunk parse error:", e)
                                pass
                        
                        for chunk in chunks:
                            tc = chunk.get('TargetContent', '')
                            rc = chunk.get('ReplacementContent', '')
                            if not tc: continue
                            total_edits += 1
                            if content.count(tc) == 1:
                                content = content.replace(tc, rc)
                                edits_applied += 1
                            elif content.count(tc) > 1:
                                content = content.replace(tc, rc, 1)
                                edits_applied += 1
                            else:
                                print(f"Missed chunk (Length {len(tc)})")
        except:
            pass

print(f"Applied {edits_applied} out of {total_edits} edits")
with open('reconstructed.html', 'w', encoding='utf-8') as f:
    f.write(content)
print("Saved to reconstructed.html")
