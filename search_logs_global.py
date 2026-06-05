import os
import json
import re

brain_dir = r'C:\Users\Administrator\.gemini\antigravity\brain'

found_chunks = []

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

for root, dirs, files in os.walk(brain_dir):
    for file in files:
        if file == 'overview.txt':
            path = os.path.join(root, file)
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    for line_num, line in enumerate(f, 1):
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
                                                    matches = re.findall(r'\{"AllowMultiple":.*? EndLine":.*?,"ReplacementContent":"(.*?)","StartLine":.*?,"TargetContent":"(.*?)"\}', chunks_str)
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
                                                tc = chunk.get('TargetContent', '')
                                                rc = chunk.get('ReplacementContent', '')
                                                if '可靠性' in tc or '可靠性' in rc or 'rel-bar' in rc or 'progress-bar' in rc or 'expected_reliability' in rc or 'expected_reliability' in tc:
                                                    found_chunks.append({
                                                        'conv': os.path.basename(os.path.dirname(os.path.dirname(path))),
                                                        'line': line_num,
                                                        'chunk': chunk
                                                    })
                            except Exception as e:
                                pass
            except Exception as e:
                pass

print(f"Total matching chunks found across all logs: {len(found_chunks)}")
for i, item in enumerate(found_chunks):
    filename = f"matching_global_{i}.txt"
    with open(filename, 'w', encoding='utf-8') as out:
        out.write(f"CONVERSATION: {item['conv']}\nLINE: {item['line']}\n\nTARGET:\n{item['chunk']['TargetContent']}\n\nREPLACEMENT:\n{item['chunk']['ReplacementContent']}\n")
    print(f"Saved {filename}")
