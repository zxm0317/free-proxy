import os

log_path = r'C:\Users\Administrator\.gemini\antigravity\brain\302ad5ad-9f40-4168-b751-5d9234b061db\.system_generated\logs\overview.txt'

if not os.path.exists(log_path):
    print("Log path not found!")
else:
    print("Searching...")
    with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    
    # Let's search for "reliability" or "rel" or progress-bar styles in index.html edits
    # Look for "expected_reliability"
    idx = content.find("expected_reliability")
    if idx != -1:
        print("Found expected_reliability at index", idx)
        print(content[idx-200:idx+200])
    else:
        print("expected_reliability not found")
        
    # Search for "可靠性"
    idx_cn = content.find("可靠性")
    if idx_cn != -1:
        print("Found 可靠性 at index", idx_cn)
        print(content[idx_cn-200:idx_cn+200])
    else:
         print("可靠性 not found")

    # Search for "strategy" or "balanced"
    idx_str = content.find("strategy")
    if idx_str != -1:
        print("Found strategy at index", idx_str)
        print(content[idx_str-200:idx_str+200])
    else:
        print("strategy not found")
