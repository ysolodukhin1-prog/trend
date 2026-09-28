"""Common declaration of management estimates, independent of source coverage."""
def result_contract(basis, missing_inputs=(), estimates=()):
    missing=list(dict.fromkeys(missing_inputs));estimated=list(dict.fromkeys(estimates))
    return dict(version='unit-result-2026-09-13.1',basis=basis,
                status='partial' if missing else 'estimate' if estimated else 'calculated',
                missing_inputs=missing,estimates=estimated,
                profit_label='Расчётная прибыль',margin_label='Маржа по учтённым затратам',
                source_completeness='unverified')


def financial_model_contract(model):
    total=model.get('total',{});tax=model.get('tax_profile',{})
    missing=[]
    if total.get('missing_cost_units',0)>0:missing.append('себестоимость части товаров')
    if tax.get('incomeTaxPct') is None:missing.append('налог')
    if tax.get('vatPct') is None:missing.append('НДС')
    estimates=['текущая себестоимость'] if model.get('basis')=='current_registry' else []
    if total.get('sku_cost_units',0)>0:estimates.append('себестоимость по единой цене SKU')
    return result_contract('period_after_configured_costs',missing,estimates)
