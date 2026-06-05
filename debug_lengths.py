import json

log_file = r'C:\Users\Administrator\.gemini\antigravity\brain\302ad5ad-9f40-4168-b751-5d9234b061db\.system_generated\logs\overview.txt'

with open(log_file, 'r', encoding='utf-8') as f:
    for line in f:
        if not line.strip(): continue
        try:
            step = json.loads(line)
            if 'tool_calls' in step:
                for call in step['tool_calls']:
                    if call['name'] == 'multi_replace_file_content':
                        args = call['args']
                        target = args.get('TargetFile', '')
                        if 'index.html' in target:
                            chunks_str = args.get('ReplacementChunks', '')
                            print("Length of chunks_str:", len(chunks_str))
                            print("Raw prefix:", repr(chunks_str[:200]))
                            print("Raw suffix:", repr(chunks_str[-200:]))
                            # Try json.loads on it
                            try:
                                json.loads(chunks_str)
                                print("json.loads SUCCESS")
                            except Exception as e:
                                print("json.loads FAILED:", e)
        except Exception as e:
            pass
