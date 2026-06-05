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
                            print("=== RAW REPLACEMENT CHUNKS STRING ===")
                            chunks_str = args.get('ReplacementChunks', '')
                            print(repr(chunks_str[:300]))
                            print("Type:", type(chunks_str))
                            exit(0)
        except Exception as e:
            pass
