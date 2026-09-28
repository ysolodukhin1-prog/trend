from pathlib import Path
p=Path('ozon_category_dashboard/frontend/src/dashboards/ProductEconomicsDashboard.tsx')
s=p.read_text(encoding='utf-8')
s=s.replace("mrc: result.valid ? result.mrc : null, rrc: result.valid ? result.rrc : null", "mrc: result.valid && !planMissing.length ? result.mrc : null, rrc: result.valid && !planMissing.length ? result.rrc : null")
s=s.replace("  const active = evaluated.find((r: Obj) => r.key === detail);", "  const active = evaluated.find((r: Obj) => r.key === detail);\n  const activePoint = view === 'prices' ? (!active?.planMissing.length ? active?.result.targets?.[planTarget] : null) : active?.result.current;")
s=s.replace(' className="text-xs" open={view === \'prices\'}',' className="text-xs"')
s=s.replace("{field('inputVat','Вычет входного НДС, ₽/продажу','Только НДС, уже включённый в расходы')}","{field('inputVat','Вычет НДС постоянных затрат, ₽','Только НДС, уже включённый в расходы')}{field('inputVatVariablePct','Вычет НДС переменных затрат, % цены','Эффективная доля цены продавца; без повторного вычета')}")
s=s.replace("{r.result.valid ? <><span>{r.assumptions.incomeTaxPct", "{r.result.valid ? <>{r.planMissing.length > 0 && <p className=\"text-amber-800\">Для целевых цен: {r.planMissing.join(', ')}</p>}<span>{r.assumptions.incomeTaxPct")
start=s.index('      {active.result.valid ? <>')
end=s.index("      {active.marketplace === 'yandex'",start)
part=s[start:end]
part=part.replace('active.result.valid ? <>','active.result.valid && activePoint ? <>')
part=part.replace('active.profit','activePoint.profit').replace('active.price','activePoint.price').replace('active.result.current.costs','activePoint.costs')
part=part.replace('Раскладка сценария',"{view === 'prices' ? `Раскладка ${planTarget === 'mrc' ? 'МРЦ' : 'РРЦ'}` : 'Раскладка сценария'}")
part=part.replace("{active.result.errors.join('; ')}", "{active.result.valid ? (active.planMissing.length ? `Для целевых цен задайте: ${active.planMissing.join(', ')}.` : active.result.quotePricing?.message || 'Целевая маржа недостижима при этих расходах.') : active.result.errors.join('; ')}")
s=s[:start]+part+s[end:]
start=s.index('<p>Маржа = результат / цена продавца.')
end=s.index('<p>Расчёт {MODEL_VERSION}',start)
s=s[:start]+'''<p>Маржа = прибыль после введённых расходов и налогов / цена продавца с НДС. МРЦ и РРЦ — минимальные цены по выбранным целям с шагом 0,01 ₽, в пределах 100 млн ₽.</p><p>Налог с доходов считается от выручки без исходящего НДС. Для налога с прибыли расходы уменьшают базу; стоимость денег не уменьшает налоговую базу. Минимальный налог задаётся отдельно. Входной НДС вычитается только если он уже включён в расходы; отрицательный НДС не означает немедленное возмещение деньгами.</p><p>Себестоимость загружается без НДС. НДС поставщика добавляется отдельно. Стоимость денег = вложенный капитал × ставка годовых × дни / 365. ДРР — доля цены продавца с НДС.</p><p>В режиме тарифов на отправку на одну конечную продажу приходится 1 / (выкуп × (1 − возвраты)) отправок. Невыкупы и возвраты после выкупа оплачиваются по отдельным введённым тарифам; списания и возврат комиссий задаются вручную. В режиме расходов периода нужны исходные проценты для изменения частоты отправок. Тарифы на отправку — явные допущения пользователя; они не становятся автоматически подтверждёнными тарифами кабинета.</p><p>Расходы периода содержат фактические услуги, сторно и компенсации в доступном источнике. Их перенос в будущее — оценка. Тарифный расчёт Яндекса заменяет только пять сопоставленных услуг и требует контрольных котировок для целевых цен. Полнота факта и применимость всех тарифов по площадкам пока не приняты.</p>''' +s[end:]
p.write_text(s,encoding='utf-8')
