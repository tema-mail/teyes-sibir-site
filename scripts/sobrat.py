"""Собирает страницы услуг из src/<slug>.html в services/<путь>/index.html.

Источник: первая строка — комментарий <!--META {...json...}-->, дальше разметка
внутри <div class="tsb">. Скрипт добавляет только использованные правила из
src/_tsb.css, мета-теги и JSON-LD (организация, услуга, крошки, FAQ из разметки).
Запуск: python3 scripts/sobrat.py
"""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
SITE = "https://teyes-sibir.ru"

ORG = {
    "@type": "AutoRepair",
    "@id": f"{SITE}/#org",
    "name": "Teyes Sibir",
    "url": f"{SITE}/",
    "telephone": "+7 995 437-92-66",
    "email": "teyes.sibir@gmail.com",
    "address": {"@type": "PostalAddress", "streetAddress": "ул. Сухарная, 35 к13, пом. 110",
                "addressLocality": "Новосибирск", "postalCode": "630001", "addressCountry": "RU"},
    "openingHoursSpecification": [{"@type": "OpeningHoursSpecification",
                                   "dayOfWeek": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
                                   "opens": "11:00", "closes": "19:00"}],
    "paymentAccepted": "Наличные, банковская карта, Долями, Сплит",
}


def used_css(css: str, body: str) -> str:
    used = {c for u in re.findall(r'class="([^"]+)"', body) for c in u.split()} | {"tsb"}

    def keep(block):
        out = []
        for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", block):
            sel = m.group(1).strip()
            classes = set(re.findall(r"\.(tsb[\w-]*)", sel))
            if classes and not classes <= used:
                continue
            out.append(f"{sel}{{{m.group(2)}}}")
        return out

    rules, pos = [], 0
    for m in re.finditer(r"@media[^{]+\{((?:[^{}]*\{[^{}]*\})*)\s*\}", css):
        rules += keep(css[pos:m.start()])
        inner = keep(m.group(1))
        if inner:
            rules.append(m.group(0).split("{")[0].strip() + "{\n  " + "\n  ".join(inner) + "\n}")
        pos = m.end()
    rules += keep(css[pos:])
    return "\n".join(rules)


def faq_from(body: str):
    pairs = re.findall(r"<summary>(.*?)</summary>\s*(?:<!--.*?-->\s*)?<p>(.*?)</p>", body, re.S)
    return [{"@type": "Question", "name": q.strip(),
             "acceptedAnswer": {"@type": "Answer", "text": re.sub(r"\s+", " ", a).strip()}} for q, a in pairs]


def build(src: pathlib.Path, css: str) -> pathlib.Path:
    raw = src.read_text(encoding="utf-8")
    meta = json.loads(re.match(r"<!--META\s*(\{.*?\})\s*-->", raw, re.S).group(1))
    body = raw[raw.index("-->") + 3:].strip()
    live_url = SITE + meta["url"]
    service = {"@type": "Service", "@id": live_url + "#service", "name": meta["service"],
               "serviceType": meta["service_type"], "provider": {"@id": ORG["@id"]},
               "areaServed": {"@type": "City", "name": "Новосибирск"}, "url": live_url}
    if meta.get("offers"):
        service["hasOfferCatalog"] = {"@type": "OfferCatalog", "name": meta["service"], "itemListElement": [
            {"@type": "Offer", "itemOffered": {"@type": "Service", "name": n},
             "priceSpecification": {"@type": "PriceSpecification", "minPrice": p, "priceCurrency": "RUB"}}
            for n, p in meta["offers"]]}
    crumbs = [("Главная", SITE + "/"), ("Услуги", SITE + "/services")] + [tuple(c) for c in meta.get("crumbs", [])]
    crumbs.append((meta["crumb"], live_url))
    graph = [ORG, service, {"@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i, "name": n, "item": u} for i, (n, u) in enumerate(crumbs, 1)]}]
    faq = faq_from(body)
    if faq:
        graph.append({"@type": "FAQPage", "mainEntity": faq})
    ld = json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False, indent=1)
    page = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<!--
  SEO-поля страницы (Tilda → Настройки страницы):
  Адрес:       {meta['url']}
  Title:       {meta['title']}
  Description: {meta['description']}
  H1:          {meta['h1']}
  Цены:        {meta.get('prices_note', '')}
-->
<title>{meta['title']}</title>
<meta name="description" content="{meta['description']}">
<meta name="robots" content="noindex, nofollow">
<meta name="color-scheme" content="dark">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap">
<style>html,body{{margin:0;background:#02050c}}</style>
</head>
<body>

<!-- ===== TILDA: в блок T123 копировать отсюда ===== -->
<div class="tsb">
<style>
{used_css(css, re.sub(r'<!--.*?-->', '', body, flags=re.S))}
</style>

{body}

<script type="application/ld+json">
{ld}
</script>
</div>
<!-- ===== TILDA: конец блока T123 ===== -->

</body>
</html>
"""
    out = ROOT / meta["path"].strip("/") / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return out


if __name__ == "__main__":
    css = (SRC / "_tsb.css").read_text(encoding="utf-8")
    for src in sorted(SRC.glob("*.html")):
        print("собрано:", build(src, css).relative_to(ROOT))
