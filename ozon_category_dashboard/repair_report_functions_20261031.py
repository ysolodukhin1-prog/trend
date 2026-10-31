import ast,pathlib,json
root=pathlib.Path('/workspace/ozon_category_dashboard');o={}
for name in ['app.py','home_marts.py','pulse_home.py','planfact.py']:
 p=root/name
 if p.exists():
  tree=ast.parse(p.read_text());o[name]=[{'name':n.name,'args':[a.arg for a in n.args.args]} for n in tree.body if isinstance(n,ast.FunctionDef) and ('home' in n.name or 'planfact' in n.name or name=='home_marts.py')]
print(json.dumps(o))