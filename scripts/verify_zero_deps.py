import sys, ast
sys.path.insert(0, '.')
from pathlib import Path

zerodep_dir = Path('zerodep')
zerodep_mods = {f.stem for f in zerodep_dir.glob('*.py')}
zerodep_mods.add('zerodep')

stdlib_mods = set(sys.stdlib_module_names) if hasattr(sys, 'stdlib_module_names') else set()
allowed = zerodep_mods | stdlib_mods

third_party_found = []
for f in sorted(zerodep_dir.glob('*.py')):
    source = f.read_text(encoding='utf-8')
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod = alias.name.split('.')[0]
                    if mod not in allowed:
                        third_party_found.append((str(f), mod))
            elif node.module:
                mod = node.module.split('.')[0]
                if mod not in allowed:
                    third_party_found.append((str(f), node.module))

if third_party_found:
    print('UNEXPECTED THIRD-PARTY IMPORTS:')
    for fp, mod in third_party_found:
        print(f'  {fp}: {mod}')
else:
    count = len(list(zerodep_dir.glob('*.py')))
    print('ZERO THIRD-PARTY RUNTIME DEPENDENCIES CONFIRMED')
    print(f'All {count} zerodep modules use only stdlib or intra-package imports.')
