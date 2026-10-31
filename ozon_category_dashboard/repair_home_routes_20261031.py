from pathlib import Path
p=Path('/workspace/ozon_category_dashboard/app.py');lines=p.read_text().splitlines()
for i,l in enumerate(lines):
 if any(x in l for x in ['home_marts','home_dashboard','/api/home','pulse_home']):print(str(i+1)+':'+l[:180])