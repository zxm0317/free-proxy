import json
import os
import glob
import re
import ast

base_file = r'c:\Users\Administrator\free-proxy\python_scripts\web\index.html'

conversation_logs = []
for path in glob.glob(r'C:\Users\Administrator\.gemini\antigravity\brain\*\.system_generated\logs\overview.txt'):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            first_line = f.readline()
            if first_line:
                step = json.loads(first_line)
                created_at = step.get('created_at', '')
                conversation_logs.append((created_at, path))
    except Exception as e:
        pass

conversation_logs.sort()

with open(base_file, 'r', encoding='utf-8') as f:
    content = f.read().replace('\r\n', '\n')

def decode_chunk_string(s):
    if not isinstance(s, str): return s
    s = s.replace('\r\n', '\n')
    if s.startswith('"') and s.endswith('"'):
        # Try to parse using ast.literal_eval as it is more tolerant
        try:
            return ast.literal_eval(s).replace('\r\n', '\n')
        except:
            val = s[1:-1]
            val = val.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\').replace('\\t', '\t')
            return val.replace('\r\n', '\n')
    return s.replace('\r\n', '\n')

def parse_chunks_list(chunks_str):
    if not isinstance(chunks_str, str):
        return chunks_str
    # Convert JSON-like string to Python-like string for ast.literal_eval
    # Replace true/false/null but be careful not to match substrings.
    # We can just do simple string replacements if we know they are JSON constants.
    py_str = chunks_str.replace(': true', ': True').replace(': false', ': False').replace(': null', ': None')
    py_str = py_str.replace(':true', ':True').replace(':false', ':False').replace(':null', ':None')
    try:
        return ast.literal_eval(py_str)
    except Exception as e:
        # Fallback to json loads with cleaned escapes
        try:
            # Escape literal control characters (newlines and tabs inside string values)
            # A simple way is to double escape backslashes first, then escape control chars.
            # But let's just try basic cleaning.
            cleaned = chunks_str.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
            return json.loads(cleaned)
        except Exception as e2:
            raise Exception(f"ast failed: {e}, json failed: {e2}")

edits_applied = 0
total_edits = 0

for time, log_file in conversation_logs:
    if time < "2026-06-01T14:07:54Z":
        continue
        
    print(f"Applying edits from {log_file} ({time})...")
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
                                        parsed = parse_chunks_list(chunks_str)
                                        for c in parsed:
                                            chunks.append({
                                                'TargetContent': decode_chunk_string(c.get('TargetContent', '')),
                                                'ReplacementContent': decode_chunk_string(c.get('ReplacementContent', ''))
                                            })
                                    except Exception as e:
                                        print("Failed to parse chunks list:", e)
                                
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
                                        # Let's try matching with normalized whitespace if direct match fails
                                        pass
                except Exception as e:
                    pass
    except Exception as e:
        print("Error reading log:", e)

print(f"Total applied: {edits_applied} out of {total_edits}")
with open(base_file, 'w', encoding='utf-8') as f:
    f.write(content)
print("Saved reconstructed file to index.html!")
