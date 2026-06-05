import json
import codecs

base_file = r'c:\Users\Administrator\free-proxy\python_scripts\web\index.html'
with open(base_file, 'r', encoding='utf-8') as f:
    content = f.read().replace('\r\n', '\n')

log_files = [
    r'C:\Users\Administrator\.gemini\antigravity\brain\302ad5ad-9f40-4168-b751-5d9234b061db\.system_generated\logs\overview.txt',
    r'C:\Users\Administrator\.gemini\antigravity\brain\cd7038d5-b5e4-49de-9e64-a6feec1278a1\.system_generated\logs\overview.txt'
]

def decode_chunk_string(s):
    if not isinstance(s, str): return s
    if s.startswith('"') and s.endswith('"'):
        # It's a JSON string.
        # Let's decode it safely.
        try:
            # We can use json.loads, but it might fail on invalid escapes.
            # If it fails, we fall back to manual replacement.
            return json.loads(s).replace('\r\n', '\n')
        except:
            # Manual unescape for JSON strings with invalid escapes
            val = s[1:-1]
            # Replace escaped newlines, quotes, etc.
            val = val.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\').replace('\\t', '\t')
            return val.replace('\r\n', '\n')
    return s.replace('\r\n', '\n')

edits_applied = 0
total_edits = 0

for log_file in log_files:
    try:
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
                                        'TargetContent': decode_chunk_string(args.get('TargetContent', '')),
                                        'ReplacementContent': decode_chunk_string(args.get('ReplacementContent', ''))
                                    }]
                                else:
                                    chunks_str = args.get('ReplacementChunks', '[]')
                                    if isinstance(chunks_str, str):
                                        if chunks_str.startswith('"'):
                                            chunks_str = decode_chunk_string(chunks_str)
                                    try:
                                        parsed = json.loads(chunks_str)
                                        for c in parsed:
                                            chunks.append({
                                                'TargetContent': decode_chunk_string(c.get('TargetContent', '')),
                                                'ReplacementContent': decode_chunk_string(c.get('ReplacementContent', ''))
                                            })
                                    except Exception as e:
                                        # Parse failure, use regex
                                        if isinstance(chunks_str, str):
                                            import re
                                            # find all {"TargetContent":"...","ReplacementContent":"..."}
                                            matches = re.findall(r'\{"AllowMultiple":.*?,"EndLine":.*?,"ReplacementContent":"(.*?)","StartLine":.*?,"TargetContent":"(.*?)"\}', chunks_str)
                                            if not matches:
                                                matches = re.findall(r'\{"TargetContent":"(.*?)","ReplacementContent":"(.*?)"\}', chunks_str)
                                            for match in matches:
                                                if len(match) == 2:
                                                    # Depending on the order of keys in JSON
                                                    rc_raw, tc_raw = match
                                                    # Need to unescape these raw strings
                                                    rc = decode_chunk_string('"' + rc_raw + '"')
                                                    tc = decode_chunk_string('"' + tc_raw + '"')
                                                    chunks.append({
                                                        'TargetContent': tc,
                                                        'ReplacementContent': rc
                                                    })
                                
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
                                        print("TargetContent not found!")
                except Exception as e:
                    pass
    except Exception as e:
        pass

print(f"Applied {edits_applied} out of {total_edits} edits")
with open('c:\\Users\\Administrator\\free-proxy\\python_scripts\\web\\index.html', 'w', encoding='utf-8') as f:
    f.write(content)
print("Saved to python_scripts/web/index.html")
