import ast
import re
import builtins  # Добавлен импорт builtins
from flask import Flask, request, jsonify
import sys
from io import StringIO

app = Flask(__name__)

BUILTINS = {
    'abs', 'all', 'any', 'ascii', 'bin', 'bool', 'bytearray', 'bytes', 'callable',
    'chr', 'classmethod', 'compile', 'complex', 'delattr', 'dict', 'dir', 'divmod',
    'enumerate', 'eval', 'exec', 'filter', 'float', 'format', 'frozenset', 'getattr',
    'globals', 'hasattr', 'hash', 'help', 'hex', 'id', 'input', 'int', 'isinstance',
    'issubclass', 'iter', 'len', 'list', 'locals', 'map', 'max', 'memoryview', 'min',
    'next', 'object', 'oct', 'open', 'ord', 'pow', 'print', 'property', 'range',
    'repr', 'reversed', 'round', 'set', 'setattr', 'slice', 'sorted', 'str', 'sum',
    'super', 'tuple', 'type', 'vars', 'zip', 'import'
}

def get_defined_and_used(code):
    try:
        tree = ast.parse(code)
        defined = set()
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        defined.add(target.id)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                used.add(node.id)
            elif isinstance(node, ast.FunctionDef):
                defined.add(node.name)
        return defined, used
    except:
        return set(), set()

def replace_variable_safely(code, old_name, new_name):
    pattern = r'\b' + re.escape(old_name) + r'\b'
    return re.sub(pattern, new_name, code)

def suggest_fixes_for_undefined_var(code, undefined_var):
    defined, _ = get_defined_and_used(code)
    suggestions = []
    
    # Варианты замены на уже определенные переменные
    for candidate in sorted(defined):
        if candidate != undefined_var:
            new_code = replace_variable_safely(code, undefined_var, candidate)
            suggestions.append({
                "label": f"заменить {undefined_var} на {candidate}",
                "code": new_code
            })
    
    # Вариант добавления новой переменной
    suggestions.append({
        "label": f"ввести переменную {undefined_var} = 0",
        "code": f"{undefined_var} = 0\n{code}"
    })
    
    return suggestions

def fix_comma_in_print(code):
    lines = code.splitlines()
    for i, line in enumerate(lines):
        if 'print(' in line and ',' not in line and line.strip().endswith(')'):
            # Проверяем, есть ли несколько аргументов без запятых
            start_idx = line.find('print(') + 6
            end_idx = line.rfind(')')
            if start_idx < end_idx:
                args_str = line[start_idx:end_idx]
                # Проверяем, есть ли пробелы между аргументами (возможно, пропущена запятая)
                if ' ' in args_str and not any(c in args_str for c in [',', '+', '-', '*', '/']):
                    # Создаем новый код с запятыми вместо пробелов
                    new_args = re.sub(r'\s+', ', ', args_str.strip())
                    lines[i] = line[:start_idx] + new_args + line[end_idx:]
                    return '\n'.join(lines), i + 1  # Возвращаем исправленный код и номер строки
    return code, None

def add_break_to_infinite_while(code):
    try:
        tree = ast.parse(code)
        lines = code.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, ast.While):
                # Проверяем, является ли цикл бесконечным
                is_infinite = False
                
                # Случай 1: while True
                if isinstance(node.test, ast.Constant) and node.test.value is True:
                    is_infinite = True
                
                # Случай 2: условие не меняется внутри цикла
                elif isinstance(node.test, ast.Compare):
                    # Собираем переменные из условия
                    cond_vars = set()
                    for cmp in ast.walk(node.test):
                        if isinstance(cmp, ast.Name):
                            cond_vars.add(cmp.id)
                    
                    # Проверяем, изменяются ли эти переменные внутри цикла
                    has_change = False
                    for stmt in node.body:
                        for n in ast.walk(stmt):
                            if isinstance(n, ast.Assign):
                                for t in n.targets:
                                    if isinstance(t, ast.Name) and t.id in cond_vars:
                                        has_change = True
                                        break
                        if has_change:
                            break
                    
                    if not has_change:
                        is_infinite = True
                
                # Если цикл бесконечный и нет break/return внутри
                if is_infinite and not any(isinstance(s, (ast.Break, ast.Return)) for s in node.body):
                    # Добавляем break в конец тела цикла
                    new_lines = lines.copy()
                    end_line = node.body[-1].lineno - 1
                    indent = ' ' * (len(lines[end_line]) - len(lines[end_line].lstrip()))
                    new_lines.insert(end_line + 1, indent + 'break')  # УБРАНО 4 ЛИШНИХ ПРОБЕЛА
                    return '\n'.join(new_lines), f"цикл бесконечный в строке {node.lineno}, добавляем break"
        
        return None, ""
    except Exception as e:
        return None, ""

def ai_debug_agent_with_options(code):
    # Сначала проверяем на бесконечные циклы
    fixed, msg = add_break_to_infinite_while(code)
    if fixed:
        return [{"label": msg, "code": fixed}]
    
    # Потом проверяем на пропущенные запятые в print
    fixed2, line_num = fix_comma_in_print(code)
    if fixed2 != code and line_num is not None:
        return [{"label": f"возможно, пропущена запятая в строке {line_num}", "code": fixed2}]
    
    # Проверяем на синтаксические ошибки
    try:
        ast.parse(code)
    except SyntaxError as e:
        lineno = e.lineno
        return [{"label": f"синтаксическая ошибка в строке {lineno}: {e.msg}", "code": code}]
    
    # Проверяем на неопределенные переменные
    defined, used = get_defined_and_used(code)
    undefined = used - defined - BUILTINS
    if undefined:
        var = sorted(undefined)[0]
        return suggest_fixes_for_undefined_var(code, var)
    
    return [{"label": "Код без ошибок", "code": code}]

@app.route('/')
def serve_index():
    with open('index.html', 'r', encoding='utf-8') as f:
        return f.read()

@app.route('/get-fixes', methods=['POST'])
def get_fixes():
    data = request.get_json()
    code = data.get('code', '')
    fixes = ai_debug_agent_with_options(code)
    return jsonify({'fixes': fixes})

@app.route('/run', methods=['POST'])
def run():
    data = request.get_json()
    code = data.get('code', '')
    inp = data.get('input', '')
    old_stdin, old_stdout = sys.stdin, sys.stdout
    sys.stdin = StringIO(inp)
    sys.stdout = captured = StringIO()
    error = ''
    try:
        # ИСПРАВЛЕНО: Используем __builtins__ вместо builtins
        exec(code, {"__builtins__": __builtins__})
    except Exception as e:
        error = str(e)
    finally:
        sys.stdin, sys.stdout = old_stdin, old_stdout
    return jsonify({"output": captured.getvalue(), "error": error})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)