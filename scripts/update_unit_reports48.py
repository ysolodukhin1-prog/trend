from pathlib import Path
p=Path('ozon_category_dashboard/frontend/src/dashboards/ProductEconomicsDashboard.tsx')
s=p.read_text(encoding='utf-8')
def replace(a,b):
 global s
 if a not in s: raise RuntimeError('Missing anchor: '+a[:120])
 s=s.replace(a,b)
replace("import { productContributions }", "import { FinancialCards, AdvancedFilters, FactualDetails, MarketplaceMatrix } from './UnitReportViews';\nimport { factualRow, expenseNames, share, emptyFilters, matchesAdvanced, csvCell, pointFor } from './unitReportModel';\nimport { planningMissing, productContributions }")
start=s.index('const esc =');end=s.index('\n',start);s=s[:start]+'const esc = csvCell;'+s[end:]
replace("const [planTarget, setPlanTarget]", "const [factBasis, setFactBasis] = useState('dated'), [allFilters, setAllFilters] = useState(false), [filters, setFilters] = useState({...emptyFilters});\n  const [planTarget, setPlanTarget]")
start=s.index('    const planMissing = [');end=s.index('    const planned =',start);s=s[:start]+"    const planMissing = planningMissing(r,assumptions);\n    const factual = factualRow(r,factBasis);\n"+s[end:]
replace('return { ...r, assumptions, result, planMissing,','return { ...r, assumptions, result, planMissing, factual, factProfit:factual.profit, factMargin:factual.margin, factCost:factual.costs.cogs, ...Object.fromEntries(Object.entries(factual.costs).map(([k,v])=>["fact_"+k,v])),')
replace('client, planTarget]);','client, planTarget, factBasis]);')
replace("evaluated.filter((r: Obj) => (market", "evaluated.filter((r: Obj) => matchesAdvanced(r,filters,view) && (market")
replace("r.profit !== null && r.profit < 0", "(view === 'facts' ? r.factProfit : view === 'promotions' ? r.promoProfit : r.profit) != null && (view === 'facts' ? r.factProfit : view === 'promotions' ? r.promoProfit : r.profit) < 0")
replace("(!r.result.valid || (view === 'prices' && (r.planMissing.length > 0 || r.planPrice == null)))", "(view === 'facts' ? r.factProfit == null : !r.result.valid || (view === 'prices' && (r.planMissing.length > 0 || r.planPrice == null)))")
replace("r.margin !== null && r.margin < r.assumptions.mrcMargin", "(view === 'facts' ? r.factMargin : view === 'promotions' ? r.promoMargin : r.margin) != null && (view === 'facts' ? r.factMargin : view === 'promotions' ? r.promoMargin : r.margin) < r.assumptions.mrcMargin")
replace('selected, sort, view]);','selected, sort, view, filters]);')
replace('[market, cabinet, search, status, view, sort]);','[market, cabinet, search, status, view, sort, filters]);')
start=s.index('  const activePoint =');end=s.index('\n',start);s=s[:start]+"  const activePoint = active ? pointFor(active,view,planTarget) : null;"+s[end:]
anchor='  const field = (key: string'
idx=s.index(anchor);s=s[:idx]+'''  function enableYandex(ones: boolean) {
    const values = {yandexQuoteMode:1,...(ones ? Object.fromEntries(Object.values(quotedServiceFields).map(x=>[x.field,1])) : {})};
    setScenario(s=> scope === 'selected' ? {...s,products:{...s.products,...Object.fromEntries(evaluated.filter(r=>r.marketplace==='yandex' && selected.includes(r.key)).map(r=>[r.key,{...s.products[r.key],...values}]))}} : {...s,markets:{...s.markets,yandex:{...s.markets.yandex,...values}}});
    if (scope !== 'selected') setScope('yandex');
  }
'''+s[idx:]
replace("onClick={() => { for (const spec of Object.values(quotedServiceFields)) change(spec.field,'1'); change('yandexQuoteMode','1'); }}", "onClick={() => enableYandex(true)}")
replace("onClick={() => change('yandexQuoteMode','1')}", "onClick={() => enableYandex(false)}")
start=s.index("  const cols: [string, string][] = view === 'facts' ?");end=s.index("    view === 'promotions' ?",start)
s=s[:start]+"  const cols: [string, string][] = view === 'facts' ? [['units','Продажи − возвраты, шт.'],['revenue','Выручка, ₽'],...Object.entries(expenseNames).map(([k,v]):[string,string]=>['fact_'+k,v+' · ₽ / %']),['factProfit','Маржинальный доход, ₽'],['factMargin','Маржа, %']] :\n"+s[end:]
replace("const columns = ['marketplace'", "const columns = view === 'facts' ? ['marketplace','cabinet','scheme','sku','article','barcodes','factBasis','units','revenue',...Object.keys(expenseNames).flatMap(k=>['fact_'+k,'share_'+k]),'factProfit','factMargin'] : ['marketplace'")
replace("esc(Array.isArray(r[k]) ? r[k].join(', ') : r[k])", "esc(k === 'factBasis' ? r.factual.basis : k.startsWith('share_') ? share(r.factual.costs[k.slice(6)],r.revenue) : Array.isArray(r[k]) ? r[k].join(', ') : r[k])")
replace("{view !== 'facts' && <section", "<FinancialCards rows={filtered} view={view} target={planTarget} />\n      {view !== 'facts' && view !== 'matrix' && <section")
replace('Убыточны в сценарии','Убыточные товары')
replace('<Button size="sm" variant="outline" onClick={exportRows}>', '<Button size="sm" variant="outline" aria-expanded={allFilters} onClick={()=>setAllFilters(v=>!v)}>Все фильтры</Button><Button size="sm" variant="outline" onClick={exportRows}>')
anchor="      {view === 'prices' && <div className=\"flex items-center"
idx=s.index(anchor);s=s[:idx]+"      {allFilters && <AdvancedFilters value={filters} onChange={setFilters} rows={evaluated} />}\n"+s[idx:]
start=s.index("      {view === 'facts' && <p");end=s.index("      {view === 'matrix' ?",start)
s=s[:start]+'''      {view === 'facts' && <div className="flex items-center gap-3 text-xs"><label>Себестоимость <select aria-label="Основа фактической себестоимости" className="rounded border p-2" value={factBasis} onChange={e=>setFactBasis(e.target.value)}><option value="dated">По датам операций</option><option value="current">Текущая — оценка</option></select></label><span className="text-muted-foreground">Статьи: сумма за период и % выручки. Маржинальный доход до налогов и внешних затрат.</span></div>}
'''+s[end:]
start=s.index("      {view === 'matrix' ? <>");end=s.index(' : <div className="max-h-[650px]',start)
s=s[:start]+"      {view === 'matrix' ? <MarketplaceMatrix groups={groups.slice((current-1)*50,current*50)} onOpen={setDetail} />"+s[end:]
replace('{r.pricingBasis}</p></td>', "{view === 'facts' ? r.factual.basis : r.pricingBasis}</p></td>")
replace("'planProfit','planMargin','profit'", "'factProfit','factMargin','planProfit','planMargin','profit'")
replace('{money(r[key])}</td>', "{money(r[key])}{key.startsWith('fact_') && <span className=\"block text-[10px] text-muted-foreground\">{money(share(r[key],r.revenue),'%')}</span>}</td>")
replace('<td className="p-2 text-[11px]">{r.result.valid ?', '<td className="p-2 text-[11px]">{view === \'facts\' ? <span>{r.factProfit == null ? \'Нет полной суммы расходов или себестоимости на выбранной основе\' : \'Начисления источника и себестоимость\'}</span> : r.result.valid ?')
replace('      <div className="grid grid-cols-3 gap-3">', "      {view === 'facts' ? <FactualDetails row={active} /> : <>\n      <div className=\"grid grid-cols-3 gap-3\">")
anchor='      <details open className="text-xs"><summary>Операции всех схем'
idx=s.index(anchor);s=s[:idx]+'      </>}\n'+s[idx:]
replace("{active.marketplace === 'yandex' && <>", "{view !== 'facts' && active.marketplace === 'yandex' && <>")
replace(": 'Раскладка сценария'", ": view === 'promotions' ? 'Раскладка акции' : 'Раскладка сценария'")
replace("[['Результат / продажу',activePoint.profit],['МРЦ',active.mrc],['РРЦ',active.rrc]]", "(view === 'promotions' ? [['До акции, ₽/ед.',active.result.current.profit],['В акции, ₽/ед.',activePoint.profit],['Изменение, ₽/ед.',activePoint.profit-active.result.current.profit]] : [['Результат / продажу',activePoint.profit],['МРЦ',active.mrc],['РРЦ',active.rrc]])")
p.write_text(s,encoding='utf-8')
print('Updated report views and review fixes')
