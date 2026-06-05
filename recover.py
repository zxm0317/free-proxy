import json

log_file = r'C:\Users\Administrator\.gemini\antigravity\brain\cd7038d5-b5e4-49de-9e64-a6feec1278a1\.system_generated\logs\overview.txt'

with open(log_file, 'r', encoding='utf-8') as f:
    for line in f:
        if not line.strip(): continue
        try:
            step = json.loads(line)
            if 'tool_responses' in step:
                for resp in step['tool_responses']:
                    if resp['name'] == 'view_file':
                        out = resp['response'].get('output', '')
                        if '1:' in out and '<html' in out:
                            # We found the file content!
                            # Clean it up by stripping the line prefixes "xxx: "
                            clean_lines = []
                            for outline in out.split('\n'):
                                if outline.startswith('The following code has been modified') or outline.startswith('The above content does NOT show') or outline.startswith('Created At:') or outline.startswith('Completed At:') or outline.startswith('File Path:') or outline.startswith('Total Lines:') or outline.startswith('Total Bytes:') or outline.startswith('Showing lines'):
                                    continue
                                # Strip line number "123: "
                                parts = outline.split(': ', 1)
                                if len(parts) == 2 and parts[0].isdigit():
                                    clean_lines.append(parts[1])
                                else:
                                    # Might be a blank line
                                    clean_lines.append(outline)
                            with open(r'c:\Users\Administrator\free-proxy\python_scripts\web\index.html', 'w', encoding='utf-8') as out_f:
                                out_f.write('\n'.join(clean_lines))
                            print("RECOVERED THE FILE COMPLETELY!!!")
                            exit(0)
        except Exception as e:
            pass
