import glob
import re

for path in glob.glob('python_scripts/**/*.py', recursive=True):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
            if 'siliconflow' in content.lower() or 'deepseek' in content.lower() or 'longcat' in content.lower():
                print(f"Found in {path}")
    except Exception as e:
        pass
