import re

with open('c:/project5/fianl4/community.html', 'r', encoding='utf-8') as f:
    content = f.read()

# 找到 script 标签内的内容
match = re.search(r'<script>(.*?)</script>', content, re.DOTALL)
if match:
    script = match.group(1)
    lines = script.split('\n')
    
    # 找出所有的 function 声明和闭合
    functions = []
    current_func = None
    brace_count = 0
    
    for i, line in enumerate(lines, 1):
        if 'function ' in line or 'async function ' in line:
            if current_func:
                functions.append((current_func, brace_count))
            current_func = (i, line.strip())
            brace_count = 0
        
        if current_func:
            brace_count += line.count('{') - line.count('}')
            if brace_count <= 0 and i > current_func[0] + 1:
                functions.append((current_func, brace_count))
                current_func = None
    
    if current_func:
        functions.append((current_func, brace_count))
    
    print("Functions found:")
    for func_info, brace in functions:
        line_num, func_name = func_info
        print(f"  Line {line_num}: {func_name[:50]}... (brace balance: {brace})")
