import re

with open("python_scripts/web/index.html", "r", encoding="utf-8") as f:
    html = f.read()

# Replace any occurrence of single quote followed by some chinese characters ending with "失?," 
html = re.sub(r"('[^']*?)璐\?,", r"\1失败',", html)
# Actually, the broken syntax in the log was '鍔犺浇宸蹭繚瀛樻ā鍨嬪け璐?,
# Which means the string lost its closing quote. Let's just fix it manually using line index.

lines = html.splitlines()
for i, line in enumerate(lines):
    if "showToast(error.message ||" in line and "error');" in line:
        if "璐?," in line or "失?," in line or "?" in line:
            lines[i] = "                showToast(error.message || 'Error loading saved model', 'error');"
            print("Fixed line", i)

# also check for any other syntax errors. Let's run a more aggressive regex.
# We will just write it back.
with open("python_scripts/web/index.html", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
