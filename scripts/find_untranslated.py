import os
import re

chinese_pattern = re.compile(r'[\u4e00-\u9fff]')

scan_dirs = ['fluent_ui', 'services', '.']

print("Scanning for non-comment, non-docstring lines containing Chinese characters...\n")

for root_dir in ['fluent_ui', 'services']:
    for root, dirs, files in os.walk(root_dir):
        for f in files:
            if f.endswith('.py'):
                path = os.path.join(root, f)
                with open(path, 'r', encoding='utf-8') as fp:
                    lines = fp.readlines()
                matches = []
                in_multiline = False
                for i, line in enumerate(lines, 1):
                    stripped = line.strip()
                    if stripped.startswith('"""') or stripped.startswith("'''"):
                        if stripped.count('"""') == 1 or stripped.count("'''") == 1:
                            in_multiline = not in_multiline
                        continue
                    if in_multiline:
                        if '"""' in stripped or "'''" in stripped:
                            in_multiline = False
                        continue
                    if stripped.startswith('#'):
                        continue
                    
                    if chinese_pattern.search(line):
                        matches.append((i, stripped))
                        
                if matches:
                    print(f"=== {path} ({len(matches)} lines) ===")
                    for idx, s in matches:
                        print(f"  {idx}: {s}")
                    print()

for f in ['main.py', 'launcher.py']:
    if os.path.exists(f):
        with open(f, 'r', encoding='utf-8') as fp:
            lines = fp.readlines()
        matches = []
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith('#'): continue
            if chinese_pattern.search(line):
                matches.append((i, stripped))
        if matches:
            print(f"=== {f} ({len(matches)} lines) ===")
            for idx, s in matches:
                print(f"  {idx}: {s}")
            print()
