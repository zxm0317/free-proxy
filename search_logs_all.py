import os
import re

brain_dir = r'C:\Users\Administrator\.gemini\antigravity\brain'

found = False

for root, dirs, files in os.walk(brain_dir):
    for file in files:
        if file == 'overview.txt':
            path = os.path.join(root, file)
            print(f"Searching in {path}...")
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    
                    # Search for Chinese text or variables in JS
                    matches1 = list(re.finditer(r'item\.rel', content))
                    matches2 = list(re.finditer(r'最聪明的', content))
                    matches3 = list(re.finditer(r'\\u53ef\\u9760\\u6027', content)) # unicode escaped "可靠性"
                    matches4 = list(re.finditer(r'\\u667a\\u529b', content)) # unicode escaped "智力"
                    
                    if matches1 or matches2 or matches3 or matches4:
                        print(f"  FOUND MATCHES in {path}!")
                        print(f"  item.rel matches: {len(matches1)}")
                        print(f"  最聪明的 matches: {len(matches2)}")
                        print(f"  reliability matches: {len(matches3)}")
                        print(f"  intelligence matches: {len(matches4)}")
                        
                        # Let's print some surrounding context of the first match
                        if matches1:
                            start = max(0, matches1[0].start() - 300)
                            end = min(len(content), matches1[0].end() + 300)
                            print("Context of item.rel:\n", content[start:end])
                        elif matches3:
                            start = max(0, matches3[0].start() - 300)
                            end = min(len(content), matches3[0].end() + 300)
                            print("Context of escaped reliability:\n", content[start:end])
                        found = True
            except Exception as e:
                print(f"Error reading {path}: {e}")

if not found:
    print("Absolutely no matches found anywhere in the brain directory logs.")
