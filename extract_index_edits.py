import os
import json
import re

log_path = r'C:\Users\Administrator\.gemini\antigravity\brain\302ad5ad-9f40-4168-b751-5d9234b061db\.system_generated\logs\overview.txt'

with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

def decode_chunk_string(s):
    if not isinstance(s, str): return s
    if s.startswith('"') and s.endswith('"'):
        try:
            return json.loads(s)
        except:
            val = s[1:-1]
            val = val.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\').replace('\\t', '\t')
            return val
    return s

all_replacements = []

for idx, line in enumerate(lines):
    if 'tool_calls' in line:
        try:
            step = json.loads(line)
            for call in step.get('tool_calls', []):
                if call.get('name') in ['replace_file_content', 'multi_replace_file_content']:
                    args = call.get('args', {})
                    target = args.get('TargetFile', '')
                    if 'index.html' in target:
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
                            except:
                                # regex fallback
                                matches = re.findall(r'\{"AllowMultiple":.*?,"EndLine":.*?,"ReplacementContent":"(.*?)","StartLine":.*?,"TargetContent":"(.*?)"\}', chunks_str)
                                for match in matches:
                                    if len(match) == 2:
                                        rc_raw, tc_raw = match
                                        rc = decode_chunk_string('"' + rc_raw + '"')
                                        tc = decode_chunk_string('"' + tc_raw + '"')
                                        chunks.append({
                                            'TargetContent': tc,
                                            'ReplacementContent': rc
                                        })
                        for chunk in chunks:
                            all_replacements.append(chunk)
        except Exception as e:
            pass

print(f"Total chunks found: {len(all_replacements)}")

# Let's save all replacements to a file so we can view them
with open('extracted_index_chunks.json', 'w', encoding='utf-8') as f:
    json.dump(all_replacements, f, ensure_ascii=False, indent=2)

print("Saved to extracted_index_chunks.json")
