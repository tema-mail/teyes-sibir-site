#!/usr/bin/env python3
"""Проверка статей агента «Автор» (teyes-avtor) перед вычиткой Артёма.

  python3 scripts/proverka_stati.py drafts/blog/2026-11-01          # все статьи дня
  python3 scripts/proverka_stati.py drafts/blog/2026-11-01/x.md     # одна статья

Блок – статью нельзя отдавать Артёму, автор правит сам. Предупреждение – показать Артёму.
Каннибализацию скрипт видит только среди черновиков блога; страницы услуг и моделей сверяет
скилл вручную (references/obnovlenie.md). Скрипт меряет форму. Правда ли написанное и захочет ли человек после статьи записаться –
решает Артём при вычитке, этого скрипт не видит.
"""
import csv, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLAN = ROOT / 'docs/plan-blog-2026-11.csv'
SITE = 'https://teyes-sibir.ru'

# Слова и обороты, которые владелец и проект уже запретили (память проекта, ARTICLE_TEMPLATE_RULES, Case Studio)
BAN = [
    (r'—', 'длинное тире – только короткое «–»'),
    (r'\bкейс', 'слово «кейс» в статьях запрещено'),
    (r'гаранти\w* (от )?(завод|производител)|заводск\w* гаранти|гаранти\w* (от )?дилер', '«гарантия завода/производителя» – не обещаем'),
    (r'\bлучш(ий|ая|ее|ие) (на рынке|в городе|в новосибирске)', 'самовосхваление'),
    (r'\bSEO\b|ключев\w+ слов', 'служебные слова в тексте'),
    (r'\d[\d\s]*\s?(тыс\.?|тысяч\w*)?\s?(₽|руб|р\.)|\bцена\s+(от\s+)?\d|\bот\s+\d[\d\s]*\s?(тыс|₽|руб|р\.)', 'цена в статье – призыв ведёт на расчёт, цену не фиксируем'),
    (r'\bданн(ый|ая|ое|ые|ого|ой|ом|ым|ых|ую|ыми)\b', 'канцелярит «данный»'),
    (r'осуществ', 'канцелярит «осуществить»'),
    (r'выполнили установку|произвели монтаж|специалисты установочного центра', 'канцелярит'),
    (r'в современном мире|не секрет, что|давайте разбер|в этой статье мы', 'пустое вступление'),
    (r'децибел|\bдБ\b', 'децибелы не обещаем'),
    (r'\$\d', 'знак доллара перед цифрой'),
]
NEED_FM = ['nomer', 'data', 'ploshchadka', 'slug', 'title', 'description', 'zapros', 'vedet', 'istochniki']


def plan_rows():
    if not PLAN.exists():
        return []
    return list(csv.DictReader(open(PLAN, encoding='utf-8')))


def parse(path):
    t = path.read_text(encoding='utf-8')
    m = re.match(r'---\n(.*?)\n---\n(.*)', t, re.S)
    if not m:
        return None, t
    fm = {}
    for line in m.group(1).splitlines():
        if ':' in line:
            k, v = line.split(':', 1)
            fm[k.strip()] = v.strip()
    return fm, m.group(2)


LAT = {'starline': 'старлайн', 'pandora': 'пандора', 'teyes': 'тейс', 'gsm': 'gsm', 'carplay': 'карплей',
       'honda': 'хонда', 'toyota': 'тойота', 'bmw': 'бмв', 'subaru': 'субару', 'hyundai': 'хендай'}


def words_of(s):
    return [w for w in re.findall(r'[a-zа-яё0-9]+', s.lower()) if len(w) > 2]


def word_in(w, text):
    """Слово запроса есть в тексте: по основе в 4 буквы (падежи), латиница – и в русском написании."""
    hg = str.maketrans('аеорсх', 'aeopcx')  # «а93» кириллицей = «a93» латиницей
    fix = lambda x: x.translate(hg) if re.search(r'\d', x) else x
    tw = {fix(t) for t in re.findall(r'[a-zа-яё0-9]+', text.lower())}
    w = fix(w)
    alts = [w, LAT.get(w, '')]
    return any(a and any(t.startswith(a[:4] if len(a) > 4 else a) for t in tw) for a in alts)


def shingles(text, n=8):
    w = re.findall(r'[а-яёa-z0-9]+', text.lower())
    return {' '.join(w[i:i + n]) for i in range(len(w) - n + 1)}


def check(path, all_zapros):
    blocks, warns = [], []
    fm, body = parse(path)
    if fm is None:
        return ['нет шапки ---…--- с полями'], []
    for k in NEED_FM:
        if not fm.get(k):
            blocks.append(f'в шапке нет поля {k}')
    dzen = fm.get('ploshchadka') == 'дзен'
    plain = re.sub(r'<!--.*?-->', '', body, flags=re.S)
    for rx, why in BAN:
        for m in re.finditer(rx, plain + ' ' + fm.get('title', '') + ' ' + fm.get('description', ''), re.I):
            blocks.append(f'{why}: «{plain[max(0, m.start() - 25):m.end() + 25].strip() if m.start() < len(plain) else m.group(0)}»')
            break
    h1 = re.findall(r'^# .+', body, re.M)
    if len(h1) != 1:
        blocks.append(f'H1 должен быть один, а их {len(h1)}')
    words = len(re.findall(r'\w+', re.sub(r'\]\([^)]*\)|https?://\S+', ' ', plain)))  # адреса ссылок – не слова
    lo, hi = (400, 900) if dzen else (700, 1600)
    if not lo <= words <= hi:
        blocks.append(f'объём {words} слов, нужно {lo}–{hi}')
    if not dzen:
        if len(fm.get('title', '')) > 70:
            blocks.append(f'title длиннее 70 знаков ({len(fm["title"])})')
        d = len(fm.get('description', ''))
        if not 120 <= d <= 170:
            blocks.append(f'description {d} знаков, нужно 120–170')
        # главный запрос – в первом абзаце после H1
        after = body[body.find(h1[0]) + len(h1[0]):] if h1 else body
        first = re.sub(r'^#.*$', '', after[:1500], flags=re.M).strip().split('\n\n')[0]
        miss = [w for w in words_of(fm.get('zapros', '')) if not word_in(w, first)]
        if miss:
            blocks.append(f'главного запроса «{fm.get("zapros")}» нет в первом абзаце (не хватает: {", ".join(miss)})')
        target = SITE + fm.get('vedet', '#')
        pos = [m.start() for m in re.finditer(re.escape(target) + r'[)/?#\s]', plain)]
        if not pos or pos[0] > len(plain) * 0.25 or pos[-1] < len(plain) * 0.75:
            blocks.append(f'ссылка на {target} нужна в первой четверти и в последней (сейчас {len(pos)} шт.)')
        faq = re.search(r'^## Вопросы.*?$(.*?)(?=^## |\Z)', body, re.S | re.M)
        q = len(re.findall(r'^### ', faq.group(1), re.M)) if faq else 0
        if not 3 <= q <= 5:
            blocks.append(f'в разделе «## Вопросы» {q} вопросов, нужно 3–5')
        if all_zapros.get(fm.get('zapros', '').lower(), 1) > 1:
            blocks.append(f'запрос «{fm.get("zapros")}» стоит главным ещё в одной статье – каннибализация')
        if '/blog/' not in body:
            warns.append('нет ссылок на соседние статьи плана (/blog/…)')
    else:
        if 'utm_source=dzen' not in body:
            blocks.append('нет ссылки на статью сайта с utm_source=dzen')
        found = list((ROOT / 'drafts/blog').glob(f'*/{fm.get("slug", "")}.md'))
        site = found[0] if found else None
        if site is None:
            warns.append('статьи сайта ещё нет в черновиках – повтор текста не проверен, публиковать Дзен только после неё')
        else:
            a, b = shingles(plain), shingles(parse(site)[1])
            share = len(a & b) / max(1, len(a))
            if share > 0.15:
                blocks.append(f'дзен повторяет статью сайта на {share:.0%} – пересказать своими словами')
    # сверка с планом
    row = next((r for r in plan_rows() if r['№'] == fm.get('nomer') and r['площадка'] == fm.get('ploshchadka')), None)
    if row is None:
        blocks.append(f'строки №{fm.get("nomer")} ({fm.get("ploshchadka")}) нет в плане {PLAN.name}')
    else:
        if row['главный запрос'].lower() != fm.get('zapros', '').lower():
            blocks.append(f'главный запрос не как в плане: «{row["главный запрос"]}»')
        if not row['адрес'].split('?')[0].endswith('/' + fm.get('slug', '')):
            blocks.append(f'адрес не как в плане: {row["адрес"]}')
        if not dzen and row['ведёт на'] != fm.get('vedet'):
            blocks.append(f'«ведёт на» не как в плане: {row["ведёт на"]}')
        if not dzen and h1 and h1[0][2:].strip() != row['заголовок'].strip():
            blocks.append(f'H1 не как заголовок плана: «{row["заголовок"]}»')
        if dzen and fm.get('title', '').strip() != row['заголовок'].strip():
            blocks.append(f'title Дзена не как в плане: «{row["заголовок"]}»')
    # то, что Артём решает сам
    todo = re.findall(r'<!--\s*ПРОВЕРИТЬ:(.*?)-->', body, re.S)
    for t in todo:
        warns.append('ПРОВЕРИТЬ:' + t.strip())
    photos = re.findall(r'!\[[^\]]*\]\(([^)]*)\)', body)
    if not photos and not re.search(r'<!--\s*ФОТО:', body):
        warns.append('нет ни фото, ни места под фото с подсказкой, какое взять')
    for p in photos:
        if not p.startswith('https://'):
            blocks.append(f'фото не по https-адресу: {p}')
    excl = re.sub(r'!\[|<!--', '', body).count('!')
    if excl > 2:
        warns.append(f'восклицательных знаков {excl}')
    return blocks, warns


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    p = pathlib.Path(sys.argv[1]).resolve()
    files = sorted(p.glob('*.md')) if p.is_dir() else [p] if p.is_file() else []
    if not files:
        print(f'нечего проверять: {p} – нет такого файла или в папке нет .md')
        return 2
    # главные запросы по всем черновикам блога – для проверки каннибализации
    allz = {}
    for f in (ROOT / 'drafts/blog').glob('*/*.md'):
        fm, _ = parse(f)
        if fm and fm.get('ploshchadka') == 'сайт':
            z = fm.get('zapros', '').lower()
            allz[z] = allz.get(z, 0) + 1
    bad = 0
    for f in files:
        b, w = check(f, allz)
        print(('БЛОК ' if b else 'ок   ') + (str(f.relative_to(ROOT)) if f.is_relative_to(ROOT) else str(f)))
        for x in b:
            print('   ✗', x)
        for x in w:
            print('   ·', x)
        bad += bool(b)
    print(f'итого {len(files)}, с блоком {bad}')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
