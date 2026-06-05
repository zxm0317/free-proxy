log_path = r'C:\Users\Administrator\.gemini\antigravity\brain\302ad5ad-9f40-4168-b751-5d9234b061db\.system_generated\logs\overview.txt'

with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
    content = f.read()

# Let's write the surrounding 2000 characters to log_context.txt
start = max(0, 15235 - 1000)
end = min(len(content), 15235 + 2500)

with open('log_context.txt', 'w', encoding='utf-8') as out:
    out.write(content[start:end])

print("Wrote context to log_context.txt")
