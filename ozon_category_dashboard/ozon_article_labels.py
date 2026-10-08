"""Russian presentation labels; original Ozon codes remain stable identifiers."""
LABELS = {
    'Acquiring': 'Эквайринг',
    'PayPerClick': 'Реклама с оплатой за клик',
    'PremiumMailingCommission': 'Комиссия за рассылку Premium',
    'Promotion': 'Продвижение',
    'ItemCompensation': 'Компенсация за товар',
    'DefectFineErrors': 'Штраф за ошибки при обработке заказа',
    'DefectFineShipmentDelayRate': 'Штраф за задержку отгрузки',
    'CrossDock': 'Кросс-докинг',
    'PackageCost': 'Стоимость упаковочных материалов',
    'PackingFee': 'Плата за упаковку',
    'SupplyInbound': 'Приёмка поставки',
    'DeliveryToHandoverPlaceByOzon': 'Доставка Ozon до места передачи',
    'LastMileCourier': 'Курьерская доставка до покупателя',
    'Logistic': 'Логистика',
    'AnalyticsPlus': 'Подписка на расширенную аналитику',
    'BrandCommission': 'Комиссия за бренд',
    'CustomerReviews': 'Отзывы покупателей',
    'Disposal': 'Утилизация',
    'Drop-Off': 'Приём отправлений в пункте сдачи',
    'Drop-Off Agent': 'Приём отправлений в партнёрском пункте сдачи',
    'InternetSiteAdvertising': 'Реклама на сайте',
    'ItemPacking': 'Упаковка товара',
    'ItemSealing': 'Опломбирование товара',
    'RealizationReportCorrection': 'Корректировка отчёта о реализации',
    'StarsMembership': 'Подписка «Звёздные товары»',
    'StockInsurance': 'Страхование товаров',
    'TemporaryPlacement': 'Временное размещение товаров',
    'VolumeWeightCharacteristicsProcessing': 'Обработка объёмно-весовых характеристик',
    'PickUpPointReturnAcceptance': 'Приём возврата в пункте выдачи',
    'ReturnFlowLogistic': 'Логистика возвратов',
    'SellerReturns': 'Возвраты продавцу',
    'Placements': 'Хранение товаров',
    'TemporaryPlacementsAgent': 'Временное размещение у партнёра',
    'PremiumSubscription': 'Подписка Premium',
}
GROUP_LABELS = {
    'acquiring': 'Эквайринг', 'advertising': 'Продвижение',
    'commission': 'Комиссия Ozon', 'compensation': 'Компенсации',
    'fines': 'Штрафы', 'fulfillment_ozon': 'Обработка и упаковка',
    'logistics': 'Логистика', 'other_service': 'Прочие услуги Ozon',
    'revenue': 'Продажи и возвраты', 'reverse_logistics': 'Логистика возвратов',
    'storage': 'Хранение', 'subscription': 'Подписки',
}


def article_label(code, line_kind='other_service'):
    code = str(code or '').strip()
    if code in LABELS:
        return LABELS[code]
    if code in GROUP_LABELS:
        return GROUP_LABELS[code]
    if any('а' <= c.lower() <= 'я' or c.lower() == 'ё' for c in code):
        return code
    return GROUP_LABELS.get(line_kind, 'Прочие начисления Ozon') + ' (тип не распознан)'
