import ast
import os
import re
import sys

def check_i18n(directory="."):
    """
    扫描指定目录下的所有 .py 文件，找出包含中文字符但未被 t() 包裹的字符串。
    自动过滤 docstring、print 和 Exception。
    """
    pattern = re.compile(r'[\u4e00-\u9fa5]')
    missing_translations = []
    
    for root, dirs, files in os.walk(directory):
        if 'venv' in root or 'scripts' in root or 'translations' in root:
            continue
            
        for file in files:
            if not file.endswith('.py'):
                continue
                
            filepath = os.path.join(root, file)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                tree = ast.parse(content)
            except Exception as e:
                print(f"无法解析文件 {filepath}: {e}")
                continue
                
            # 1. 收集 docstrings
            docs = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef, ast.Module)):
                    doc = ast.get_docstring(node)
                    if doc:
                        docs.add(doc)
                        
            # 2. 收集 print 和 Exception 中的字符串
            ignored_strings = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    is_print = isinstance(node.func, ast.Name) and node.func.id == 'print'
                    is_exception = isinstance(node.func, ast.Name) and node.func.id in ('Exception', 'ValueError', 'TypeError')
                    if is_print or is_exception:
                        for arg in ast.walk(node):
                            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                                ignored_strings.add(arg.value)
                                
            # 3. 收集已经被 t() 包裹的字符串
            translated_strings = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name) and node.func.id == 't':
                        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                            translated_strings.add(node.args[0].value)
                            
            # 4. 找出所有包含中文的字符串，并过滤
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    val = node.value
                    if pattern.search(val):
                        if val in docs or val in ignored_strings or val in translated_strings:
                            continue
                        missing_translations.append((filepath, getattr(node, 'lineno', '?'), val))
                        
    return missing_translations

if __name__ == "__main__":
    print("开始扫描未翻译的中文文案...")
    missing = check_i18n()
    
    if not missing:
        print("太棒了！没有发现未翻译的中文文案。")
        sys.exit(0)
        
    print(f"\n发现 {len(missing)} 处疑似漏翻译的文案：")
    print("-" * 50)
    
    # 按文件分组输出
    grouped = {}
    for filepath, lineno, val in missing:
        if filepath not in grouped:
            grouped[filepath] = []
        grouped[filepath].append((lineno, val))
        
    for filepath, items in grouped.items():
        print(f"\n文件: {filepath}")
        for lineno, val in sorted(items, key=lambda x: x[0] if isinstance(x[0], int) else 0):
            # 截断过长的字符串
            display_val = val if len(val) <= 50 else val[:47] + "..."
            print(f"  行 {lineno}: {repr(display_val)}")
            
    print("-" * 50)
    print(f"总计: {len(missing)} 处")