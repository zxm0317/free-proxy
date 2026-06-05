import json

log_file = r'C:\Users\Administrator\.gemini\antigravity\brain\302ad5ad-9f40-4168-b751-5d9234b061db\.system_generated\logs\overview.txt'
out_file = r'C:\Users\Administrator\free-proxy\extracted_chunks.txt'

with open(log_file, 'r', encoding='utf-8') as f:
    lines = f.readlines()

output = []
for line in lines:
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
                    
                    if call['name'] == 'replace_file_content':
                        tc = args.get('TargetContent', '')
                        rc = args.get('ReplacementContent', '')
                        output.append("=== REPLACE ===")
                        output.append("TARGET:\n" + repr(tc[:100]))
                        output.append("REPLACEMENT:\n" + rc)
                        output.append("================\n")
                    else:
                        chunks_str = args.get('ReplacementChunks', '[]')
                        # It might be a list of dicts, or a JSON string.
                        if isinstance(chunks_str, str):
                            if chunks_str.startswith('['):
                                # try to parse the JSON array
                                try:
                                    parsed = json.loads(chunks_str)
                                    for c in parsed:
                                        tc = c.get('TargetContent', '')
                                        rc = c.get('ReplacementContent', '')
                                        output.append("=== MULTI REPLACE ===")
                                        output.append("TARGET:\n" + repr(tc[:100]))
                                        output.append("REPLACEMENT:\n" + rc)
                                        output.append("================\n")
                                except Exception as e:
                                    output.append("FAILED TO PARSE CHUNKS_STR: " + str(e))
                        elif isinstance(chunks_str, list):
                            for c in chunks_str:
                                tc = c.get('TargetContent', '')
                                rc = c.get('ReplacementContent', '')
                                output.append("=== MULTI REPLACE ===")
                                output.append("TARGET:\n" + repr(tc[:100]))
                                output.append("REPLACEMENT:\n" + rc)
                                output.append("================\n")
    except Exception as e:
        pass

with open(out_file, 'w', encoding='utf-8') as f:
    f.write('\n'.join(output))
