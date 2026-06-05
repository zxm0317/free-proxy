import json

with open('extracted_index_chunks.json', 'r', encoding='utf-8') as f:
    chunks = json.load(f)

print(f"Total chunks: {len(chunks)}")

matching = []
for i, c in enumerate(chunks):
    tc = c.get('TargetContent', '')
    rc = c.get('ReplacementContent', '')
    if '可靠性' in tc or '可靠性' in rc or 'rel-bar' in rc or 'progress-bar' in rc or 'expected_reliability' in rc or 'expected_reliability' in tc:
        matching.append((i, c))

print(f"Matching chunks: {len(matching)}")
for idx, c in matching:
    print(f"\n--- MATCHING CHUNK INDEX {idx} ---")
    # Save the chunk to a file so it can be viewed safely without console encoding issues
    filename = f'matching_chunk_{idx}.txt'
    with open(filename, 'w', encoding='utf-8') as out:
        out.write(f"TARGET:\n{c.get('TargetContent')}\n\nREPLACEMENT:\n{c.get('ReplacementContent')}\n")
    print(f"Saved match to {filename}")
