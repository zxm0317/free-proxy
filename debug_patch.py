import json

base_file = r'c:\Users\Administrator\free-proxy\python_scripts\web\index.html'
with open(base_file, 'r', encoding='utf-8') as f:
    content = f.read().replace('\r\n', '\n')

log_file = r'C:\Users\Administrator\.gemini\antigravity\brain\302ad5ad-9f40-4168-b751-5d9234b061db\.system_generated\logs\overview.txt'

def decode_chunk_string(s):
    if not isinstance(s, str): return s
    s = s.replace('\r\n', '\n')
    if s.startswith('"') and s.endswith('"'):
        try:
            return json.loads(s).replace('\r\n', '\n')
        except:
            val = s[1:-1]
            val = val.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\').replace('\\t', '\t')
            return val.replace('\r\n', '\n')
    return s.replace('\r\n', '\n')

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
                                pass
                        
                        for chunk in chunks:
                            tc = chunk.get('TargetContent', '')
                            if tc:
                                print("--- SEARCHING FOR ---")
                                print(repr(tc[:150]))
                                print("In file count:", content.count(tc))
                                if content.count(tc) == 0:
                                    # Let's find close matches
                                    # Print first 50 chars and check if they exist in file
                                    print("First 50 chars exists:", tc[:50] in content)
                                    # If not, find which part exists
                                    for length in [50, 40, 30, 20, 10]:
                                        if tc[:length] in content:
                                            print(f"Prefix of length {length} exists: {repr(tc[:length])}")
                                            break
                                exit(0) # Just check the first one
        except Exception as e:
            pass
