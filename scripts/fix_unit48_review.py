"""Apply only after independent v4.8 freeze release."""
from pathlib import Path
p=Path('ozon_category_dashboard/frontend/src/dashboards/yandexQuotedEconomics.ts')
s=p.read_text(encoding='utf-8')
assert 'const point = { ...base.current, price, costs:' in s
s=s.replace('const point = { ...base.current, price, costs:', 'const point = { ...base.current, price, priceRaw: price, costs:')
p.write_text(s,encoding='utf-8')
p=Path('ozon_category_dashboard/frontend/src/dashboards/ProductEconomicsDashboard.tsx')
s=p.read_text(encoding='utf-8')
s=s.replace('const [scenario, setScenario] = useState<Scenario>(fresh)', 'const [scenario, setScenarioState] = useState<Scenario>(fresh)')
anchor='  const [yandexQuotes, setYandexQuotes]'
assert anchor in s
s=s.replace(anchor,'''  const hypothesisLocked = !!project && project.status !== 'draft';
  const hypothesisLockRef = useRef(hypothesisLocked); hypothesisLockRef.current = hypothesisLocked;
  const setScenario: React.Dispatch<React.SetStateAction<Scenario>> = update => {
    if (!hypothesisLockRef.current) setScenarioState(update);
  };
'''+anchor)
s=s.replace('JSON.stringify([client, dateFrom, dateTo, scenario])','JSON.stringify([client, dateFrom, dateTo, scenario, project?.project_id, project?.revision, project?.status])')
s=s.replace('[client, dateFrom, dateTo, scenario, data]);','[client, dateFrom, dateTo, scenario, data, project?.project_id, project?.revision, project?.status]);')
s=s.replace('setFilters({...emptyFilters}); setScenario(fresh());', 'setFilters({...emptyFilters}); setScenarioState(fresh());')
s=s.replace('scenario={scenario} setScenario={setScenario}', 'scenario={scenario} setScenario={setScenarioState}')
s=s.replace("disabled={!!project && project.status!=='draft'}", 'disabled={hypothesisLocked}')
s=s.replace("      {view === 'facts' ? <FactualDetails", "      <fieldset disabled={hypothesisLocked} className=\"space-y-3\">\n      {hypothesisLocked && <p className=\"text-xs\">Гипотеза запущена: изменение вводных и котировок доступно в новой гипотезе.</p>}\n      {view === 'facts' ? <FactualDetails")
s=s.replace('    </section></div>}', '    </fieldset></section></div>}')
s=s.replace('onQuotes={quotes => setYandexQuotes(v => ({ ...v, [active.key]: quotes }))}', 'onQuotes={quotes => { if (!hypothesisLockRef.current) setYandexQuotes(v => ({ ...v, [active.key]: quotes })); }}')
p.write_text(s,encoding='utf-8')
print('Applied UE27 target-price synchronization and UE28 hypothesis UI mutation guard')
