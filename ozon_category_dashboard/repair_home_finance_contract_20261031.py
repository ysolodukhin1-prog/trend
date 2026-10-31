import pathlib,ast
p=pathlib.Path('/workspace/ozon_category_dashboard/home_marts.py');s=p.read_text();t=ast.parse(s)
for n in t.body:
 if isinstance(n,ast.FunctionDef) and n.name in ['home_pl_payload','_statement']:
  text=ast.get_source_segment(s,n);print(text[-5500:])