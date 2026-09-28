from __future__ import annotations
import argparse, json, re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ALIAS={"Matrix":"Matrix Sport","Demount":"De Mount"}

def txt(v:Any)->str:
    return re.sub(r"\s+"," ","" if v is None else str(v)).strip()

def parts(v:Any)->list[str]:
    return [x.strip() for x in txt(v).split(";") if x.strip() and x.strip() not in ("Не выявлены","Не определено")]

def labels(rows:list[dict[str,Any]])->str:
    return "; ".join(f"{x['label']} ({x['count']})" for x in rows[:3])

def review_cat(p:dict[str,Any])->str:
    s=" ".join(txt(p.get(k)).lower() for k in ("category_cluster","source_category","product_name"))
    if "резинов" in s and ("сапог" in s or "ботин" in s): return "Резиновые сапоги"
    if "пухов" in s: return "Пуховики"
    if "ветров" in s: return "Ветровки"
    if "куртк" in s or "анорак" in s or "парка" in s: return "Куртки"
    if "ботин" in s or "сапог" in s: return "Ботинки"
    if "кроссов" in s: return "Кроссовки"
    if "кед" in s: return "Кеды"
    if "сандал" in s: return "Сандалии"
    if "купаль" in s or "плавки" in s: return "Костюмы купальные"
    if "костюм" in s: return "Костюмы спортивные"
    if "брюк" in s or "джоггер" in s or "леггин" in s: return "Брюки спортивные" if "спорт" in s else "Брюки"
    if "футбол" in s or "майк" in s or "лонгслив" in s: return "Футболки спортивные"
    return txt(p.get("category_cluster"))

def motif(p:dict[str,Any])->str:
    s=" ".join(txt(p.get(k)).lower().replace("ё","е") for k in ("functional_segment","activity_scenario","technologies","functional_properties","construction_features","key_feature","product_meaning","style_profile"))
    checks=[
      ("Влагозащита и защита от ветра",("водонепрониц","водоотталк","непромок","дожд","мембран","ветрозащ","защита от ветра")),
      ("Тепло и защита от холода",("утепл","тепл","мороз","пух","термо")),
      ("Сцепление и проходимость",("сцеплен","протектор","антискольз","нескольз","traction","шип")),
      ("Амортизация и мягкость хода",("амортиз","эва","eva","смягч","пена")),
      ("Вентиляция и влагоотведение",("вентиляц","воздухопрониц","влагоотвод","дышащ")),
      ("Поддержка и стабилизация",("поддерж","стабилиз","фиксац","компресс")),
      ("Свобода движения и эластичность",("эластич","свобода движ","стрейч","stretch")),
      ("Легкость и мобильность",("легк","компакт","складн","малый вес")),
      ("Износостойкость и практичность",("износостой","прочн","усилен","долговеч"))]
    for name,needles in checks:
        if any(x in s for x in needles): return name
    if any(x in s for x in ("бег","фитнес","футбол","теннис","плаван","трениров")): return "Специализация под вид спорта"
    if "outdoor" in s or any(x in s for x in ("поход","туризм","треккинг")): return "Outdoor-универсальность"
    if any(x in s for x in ("lifestyle","sportstyle","повседнев","город")): return "Повседневная универсальность и sportstyle"
    if any(x in s for x in ("принт","логотип","ярк","контраст")): return "Выразительный визуальный код"
    return "Универсальная базовая функциональность"

def feature(p:dict[str,Any],m:str)->tuple[str,str]:
    activity=txt(p.get("activity_scenario")) or "основного сценария категории"
    core="; ".join(parts(p.get("key_feature"))[:3]) or txt(p.get("product_meaning"))
    short=f"{m} для сценария «{activity}»"+(f": {core.lower()}" if core else "")+"."
    visual=", ".join(x for x in (txt(p.get("style_profile")),txt(p.get("color_group")),txt(p.get("print_pattern"))) if x and x not in ("Не определено","Не выявлен"))
    conclusion=f"Товар продает не только форму «{txt(p.get('category_cluster'))}», а обещание «{m.lower()}». {txt(p.get('product_meaning'))}"
    if visual: conclusion+=f" Визуальный код: {visual}."
    conclusion+=f" Релевантный конкурент должен совпадать по сценарию «{activity}» и ключевой функции, а не только по категории."
    return short,re.sub(r"\s+"," ",conclusion).strip()

def load_client(path:Path):
    values=None
    for line in path.read_text(encoding="utf-8").splitlines():
        row=json.loads(line)
        if row.get("kind")=="table" and row.get("sheet")=="Спортмастер": values=row["values"]
    if not values:return [],{}
    h=values[0]; out=[]; lookup={}
    for raw in values[1:]:
        item=dict(zip(h,raw))
        pos=sorted(((k.replace("Преимущество: ",""),float(v or 0)) for k,v in item.items() if k.startswith("Преимущество:")),key=lambda x:x[1],reverse=True)
        neg=sorted(((k.replace("Недостаток: ",""),float(v or 0)) for k,v in item.items() if k.startswith("Недостаток:")),key=lambda x:x[1],reverse=True)
        x={"platform":item["Площадка"],"category":item["Товарная категория"],"brand":item["Бренд"],"analyzed_reviews":int(item["Проанализировано отзывов"] or 0),"negative_share":float(item["Доля негативных отзывов"] or 0),"avg_rating":item["Средний рейтинг карточек"],"top_positive":[{"label":a,"share":b} for a,b in pos[:3] if b],"top_negative":[{"label":a,"share":b} for a,b in neg[:3] if b]}
        out.append(x);lookup[(x["brand"],x["category"])]=x
    return out,lookup

def proxy_text(p):
    if not p:return "Категорийный proxy отзывов отсутствует."
    pos=", ".join(f"{x['label'].lower()} {x['share']:.1%}" for x in p["top_positive"])
    neg=", ".join(f"{x['label'].lower()} {x['share']:.1%}" for x in p["top_negative"])
    result=f"В WB-агрегации бренд×категория проанализировано {p['analyzed_reviews']:,} отзывов"
    if pos:result+=f"; чаще отмечают {pos}"
    if neg:result+=f"; риски — {neg}"
    return result+"."

BRANDS=[
{"brand":"Demix","public_fact":"Бренд развивает доступную экипировку для спорта и тренировок; в коммуникации заметны бег, фитнес, атлеты и современный дизайн.","semantic_code":"Performance для массового спорта + понятная технологичность + актуальный sportstyle.","competitor_implication":"Сравнивать отдельно с performance-моделями по функции и со sportstyle-моделями по образу.","source":"https://www.demix.ru/pages/about/","source_type":"Официальный сайт бренда"},
{"brand":"Fila","public_fact":"История бренда начинается в Биелле в 1911 году; позиционирование соединяет спортивное наследие, итальянскую эстетику и lifestyle.","semantic_code":"Heritage sportstyle: спортивная функция смягчена модным, узнаваемым образом.","competitor_implication":"Искать конкурентов на пересечении sportswear, ретро-кодов и городской носки.","source":"https://www.fila.com.br/institucional/historia","source_type":"Официальный сайт бренда"},
{"brand":"Kappa","public_fact":"Kappa использует платформу Born in Sport и сочетает performance, дизайн, традицию и современный lifestyle.","semantic_code":"Итальянский спортивный heritage, логомания и переход от спорта к улице.","competitor_implication":"Релевантны бренды с сильным визуальным кодом и спортивной аутентичностью.","source":"https://www.kappa.com/en-us","source_type":"Официальный сайт бренда"},
{"brand":"Outventure","public_fact":"Бренд создан в 2003 году вокруг любви к природе, походам и водным путешествиям; акцентирует технологии, универсальность и широкий функционал.","semantic_code":"Понятный outdoor для семьи и массового пользователя: практичность без избыточной профессиональности.","competitor_implication":"Сопоставлять по реальному сценарию на природе, погодной защите, универсальности и цене.","source":"https://www.sportmaster.ru/promo/54452515/","source_type":"Официальная страница Спортмастера"},
{"brand":"Northland","public_fact":"Бренд связывает происхождение с австрийскими альпинистами и туристами и описывает многослойную экипировку для разных погодных условий.","semantic_code":"Альпийский outdoor: слои, погода, тепло, мембраны и надежность.","competitor_implication":"Главный фильтр — уровень погодной защиты и outdoor-техническости, затем стиль.","source":"https://northland-pro.com/about/","source_type":"Официальный сайт бренда"},
{"brand":"Matrix","public_fact":"В открытых источниках подтверждается регистрация товарного знака MATRIX SPORT в 2025 году; развернутая официальная легенда бренда не найдена.","semantic_code":"По текущей выборке — базовая женская спортивная одежда; вывод предварительный из-за 3 товаров.","competitor_implication":"Не переносить велосипедную выдачу на одежду; сравнивать только по конкретной товарной группе и карточкам.","source":"https://companies.rbc.ru/trademark/1115192/matrix-sport/","source_type":"Открытый реестр через РБК Компании"},
{"brand":"Demount","public_fact":"Товарный знак DEMOUNT / DE MOUNT зарегистрирован с 2010 года для одежды, обуви и аксессуаров; отдельная публичная бренд-платформа не найдена.","semantic_code":"По ассортиментным следам — outdoor/utility; брендовый смысл требует подтверждения клиентом.","competitor_implication":"Определять конкурентов от функции конкретной категории, не достраивая неподтвержденную легенду бренда.","source":"https://brand-search.ru/trademarks/demount-de-mount-423760/","source_type":"Открытые данные товарного знака"}]

def main():
    a=argparse.ArgumentParser()
    a.add_argument("--meanings",required=True);a.add_argument("--wb-reviews",required=True);a.add_argument("--client-reviews",required=True);a.add_argument("--output",required=True)
    z=a.parse_args()
    base=json.loads(Path(z.meanings).read_text(encoding="utf-8"))
    wb=json.loads(Path(z.wb_reviews).read_text(encoding="utf-8"))["products"]
    client_rows,client_lookup=load_client(Path(z.client_reviews))
    products=base["products"];enriched=[]
    print(f"ПЛАН: {len(products)} товаров | отзывы WB | категорийный proxy | сводки | 7 брендов")
    for i,p0 in enumerate(products,1):
        p=dict(p0);m=motif(p);short,conclusion=feature(p,m);cat=review_cat(p)
        proxy=client_lookup.get((ALIAS.get(p["brand"],p["brand"]),cat))
        exact=wb.get(str(p.get("sku"))) if p.get("marketplace")=="WB" else None
        p.update({"semantic_motif":m,"semantic_distinctive_feature":short,"product_semantic_conclusion":conclusion,"review_category":cat,"review_proxy":proxy,"review_layer":exact or {}})
        if exact:
            p.update({"review_source_level":"Точный артикул WB + категорийный контекст" if proxy else "Точный артикул WB","review_use_case":exact.get("main_use_case") or "Сценарий явно не выражен","review_behavior_patterns":labels(exact.get("top_use_cases") or []) or "Явные поведенческие паттерны не выделены","review_valued":labels(exact.get("top_positive_topics") or []),"review_risks":labels(exact.get("top_negative_topics") or []),"review_conclusion_final":exact.get("review_conclusion") or proxy_text(proxy),"review_excerpt":exact.get("positive_excerpt") or "Нет содержательной положительной выдержки по точному SKU.","review_negative_excerpt":exact.get("negative_excerpt") or "Нет содержательной негативной выдержки по точному SKU."})
        else:
            p.update({"review_source_level":"Категорийный proxy WB для Ozon" if proxy else "Тексты отзывов по SKU недоступны","review_use_case":"Не определяется без текстов отзывов по SKU","review_behavior_patterns":"Нет SKU-текстов; не экстраполируется","review_valued":"; ".join(x["label"] for x in (proxy or {}).get("top_positive",[])),"review_risks":"; ".join(x["label"] for x in (proxy or {}).get("top_negative",[])),"review_conclusion_final":proxy_text(proxy),"review_excerpt":"Дословная выдержка по SKU Ozon недоступна; используется только агрегат тем категории.","review_negative_excerpt":"Дословная выдержка по SKU Ozon недоступна."})
        enriched.append(p)
        if i%250==0 or i==len(products):print(f"ПРОГРЕСС: {i}/{len(products)} ({i/len(products):.1%}) | товары обогащены")
    groups=defaultdict(list)
    for p in enriched:groups[(p["brand"],p["category_cluster"])].append(p)
    sem=[];reviews=[]
    for (brand,cat),rows in sorted(groups.items()):
        sales=sum(float(x.get("ordered_amount_rub") or 0) for x in rows);ms=Counter()
        for x in rows:ms[x["semantic_motif"]]+=float(x.get("ordered_amount_rub") or 0)
        dominant=ms.most_common(3)
        dominant_text="; ".join(f"{n} ({v/sales:.1%} продаж)" if sales else n for n,v in dominant)
        examples=" | ".join(f"{x['marketplace']} {x['sku']}: {txt(x['product_name'])}" for x in sorted(rows,key=lambda x:float(x.get("ordered_amount_rub") or 0),reverse=True)[:3])
        sem.append({"brand":brand,"category":cat,"sku_count":len(rows),"sales_rub":sales,"dominant_semantic_features":dominant_text,"category_semantic_conclusion":f"Ядро категории у {brand}: {dominant_text}.","example_products":examples})
        exact=[x for x in rows if x["marketplace"]=="WB" and x.get("review_layer")]
        uc=Counter();pos=Counter();neg=Counter()
        for x in exact:
            for y in x["review_layer"].get("top_use_cases") or []:uc[y["label"]]+=int(y["count"])
            for y in x["review_layer"].get("top_positive_topics") or []:pos[y["label"]]+=int(y["count"])
            for y in x["review_layer"].get("top_negative_topics") or []:neg[y["label"]]+=int(y["count"])
        review_count=sum(int(x["review_layer"].get("review_count") or 0) for x in exact)
        text_count=sum(int(x["review_layer"].get("informative_reviews") or 0) for x in exact)
        ratings=[(float(x["review_layer"]["avg_rating"]),int(x["review_layer"].get("review_count") or 0)) for x in exact if x["review_layer"].get("avg_rating")]
        avg=round(sum(r*max(n,1) for r,n in ratings)/sum(max(n,1) for _,n in ratings),2) if ratings else None
        proxy=next((x.get("review_proxy") for x in rows if x.get("review_proxy")),None)
        ranked=sorted(exact,key=lambda x:float(x.get("ordered_amount_rub") or 0),reverse=True)
        pe=next((x["review_layer"].get("positive_excerpt") for x in ranked if x["review_layer"].get("positive_excerpt")),"")
        ne=next((x["review_layer"].get("negative_excerpt") for x in ranked if x["review_layer"].get("negative_excerpt")),"")
        cp=[]
        if uc:cp.append("Сценарии: "+", ".join(n.lower() for n,_ in uc.most_common(3)))
        if pos:cp.append("ценят: "+", ".join(n.lower() for n,_ in pos.most_common(3)))
        if neg:cp.append("риски: "+", ".join(n.lower() for n,_ in neg.most_common(3)))
        reviews.append({"brand":brand,"category":cat,"sku_total":len(rows),"wb_skus_with_exact_reviews":len(exact),"exact_review_count":review_count,"informative_texts_loaded":text_count,"weighted_avg_rating":avg,"top_use_cases":"; ".join(f"{k} ({v})" for k,v in uc.most_common(3)),"top_positive":"; ".join(f"{k} ({v})" for k,v in pos.most_common(3)),"top_negative":"; ".join(f"{k} ({v})" for k,v in neg.most_common(3)),"category_review_conclusion":"; ".join(cp)+("." if cp else proxy_text(proxy)),"representative_positive_excerpt":pe or "Нет дословной выдержки по точным SKU.","representative_negative_excerpt":ne or "Нет дословной негативной выдержки по точным SKU.","proxy_review_count":(proxy or {}).get("analyzed_reviews"),"proxy_negative_share":(proxy or {}).get("negative_share"),"proxy_avg_rating":(proxy or {}).get("avg_rating"),"proxy_summary":proxy_text(proxy),"coverage_note":"Точные тексты — только WB; Ozon не экстраполируется на SKU. Категорийный proxy явно помечен."})
    output={"generated_at":datetime.now().isoformat(timespec="seconds"),"quality":{"products":len(enriched),"wb_exact_skus":sum(x["marketplace"]=="WB" for x in enriched),"wb_skus_with_text":sum(bool(x.get("review_layer",{}).get("informative_reviews")) for x in enriched),"ozon_skus":sum(x["marketplace"]=="Ozon" for x in enriched),"ozon_with_category_proxy":sum(x["marketplace"]=="Ozon" and bool(x.get("review_proxy")) for x in enriched),"brand_category_groups":len(groups)},"methodology":["Смысловая особенность строится из описания, характеристик, функции, сценария и визуального кода товара.","WB: отзывы фильтруются по точному nmId; отзывы других вариантов не переносятся.","Ozon: без авторизованной выгрузки тексты по SKU недоступны; используется только явно помеченный WB proxy бренд×категория.","Выдержки обезличены: авторы и идентификаторы не сохраняются."],"products":enriched,"semantic_summary":sem,"review_summary":reviews,"client_review_aggregates":client_rows,"brand_research":BRANDS}
    out=Path(z.output);out.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"ИТОГ: {len(enriched)} товаров | {len(sem)} бренд-категорий | файл {out}")

if __name__=="__main__":main()
