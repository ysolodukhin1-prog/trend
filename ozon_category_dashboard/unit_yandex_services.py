"""Source-backed service direction for Yandex unit economics.

Sheet names alone do not identify cashflow direction: middle mile contains
customer and return legs, and processing sheets contain return handling.
Names below are observed in the stored RU reports and match the official
service report contract. Unknown names remain in residual costs, not zero.
"""

VERSION = 'yandex-services-2026-09-12.1'
DOCUMENTATION = 'https://yandex.ru/dev/market/partner-api/doc/ru/reference/reports/generateUnitedMarketplaceServicesReport'
RETURN_RULES = 'https://yandex.ru/support/marketplace/ru/introduction/rates/models/fby#returns'
FIELDS = ('commission_cost', 'acquiring_cost', 'logistics_cost', 'reverse_cost',
          'fulfillment_cost', 'storage_cost', 'advertising_cost')


def classify(service_type, service_name):
    """(portfolio field or residual, semantic label, ambiguous direction)."""
    kind = str(service_type or '')
    name = ' '.join(str(service_name or '').lower().split())
    if kind == 'placement':
        return 'commission_cost', 'Размещение', False
    if kind in ('payment_transfer', 'payment_accepting'):
        return 'acquiring_cost', 'Платёжные услуги', False
    if kind == 'crossregional_delivery':
        if name == 'доставка покупателю (средняя миля)':
            return 'logistics_cost', 'Прямая средняя миля', False
        if name == 'доставка невыкупов и возвратов':
            return 'reverse_cost', 'Обратная средняя миля', False
        return 'other', 'Направление средней мили не подтверждено', True
    if kind in ('delivery', 'express_delivery'):
        if name in ('доставка покупателю', 'экспресс-доставка покупателю'):
            return 'logistics_cost', 'Доставка покупателю', False
        return 'other', 'Направление доставки не подтверждено', True
    if kind in ('order_processing', 'order_processing_on_warehouse'):
        if name == 'обработка невыкупа или возврата':
            return 'reverse_cost', 'Обработка невыкупов и возвратов', False
        return 'other', 'Назначение обработки не подтверждено', True
    if kind == 'export_from_warehouse':
        return 'other', 'Вывоз со склада, не возврат покупателя', False
    if kind == 'delivery_via_transit_warehouse':
        return 'other', 'Транзитная поставка, не доставка покупателю', False
    if kind.startswith('paid_storage') or kind == 'storage_of_returns':
        return 'storage_cost', 'Хранение', False
    if kind in ('boost', 'cpm-boost', 'shelf', 'product-banners'):
        return 'advertising_cost', 'Продвижение', False
    return 'other', 'Прочие услуги источника', False


def apply_service_components(rows, services):
    """Reclassify costs only. Revenue, quantities and settlement net stay intact."""
    by_identity = {(row['cabinet'], row['sku']): row for row in rows}
    for row in rows:
        row.update({field: 0 for field in FIELDS})
        row['service_components'] = []
        row['service_components_version'] = VERSION
        row['unclassified_service_rows'] = 0
    for entry in services:
        row = by_identity[(entry['cabinet'], entry['sku'])]
        field, label, unknown = classify(entry['service_type'], entry['service_name'])
        amount = entry['amount']
        # NULL source amounts are already counted in missing_amount by flows.
        # They also remain visibly unknown in the detailed ledger.
        if field != 'other' and amount is not None:
            row[field] += amount
        row['service_components'].append(dict(entry, component=field, component_label=label,
            amount=None if entry['missing_amount'] else amount, direction_unknown=unknown))
        if unknown:
            row['unclassified_service_rows'] += entry['source_rows']
    for row in rows:
        if row['unclassified_service_rows']:
            row['source_warnings'].append('Назначение части доставки/обработки не подтверждено; суммы сохранены в прочих расходах. Изменение выкупа, возвратов или логистики требует классификации этих операций.')
