from pathlib import Path
import ast
p=Path('/workspace/ozon_category_dashboard/app.py');lines=p.read_text().splitlines()
for start,end in [(11643,11678),(20515,20557),(14282,14303)]:print('\n'.join(f'{i+1}:{lines[i]}' for i in range(start,end)))
p=Path('/workspace/ozon_category_dashboard/home_marts.py');tree=ast.parse(p.read_text())
for n in tree.body:
 if isinstance(n,ast.FunctionDef) and n.name=='category_payload':print(ast.get_source_segment(p.read_text(),n)[-4500:])