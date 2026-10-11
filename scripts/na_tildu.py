"""Переносит страницу из репозитория на teyes-sibir.ru (Tilda) напрямую через MCP Tilda.

Код страницы уходит в Tilda файлом: скрипт сам вызывает MCP-сервер по HTTP,
модель его не пересказывает. Страница целиком — секунды.

Запуск из корня репозитория:
  python3 scripts/na_tildu.py services/closers             # черновик: записать в Tilda, не публиковать
  python3 scripts/na_tildu.py services/closers --check     # только подготовить и проверить, в Tilda не писать
  python3 scripts/na_tildu.py services/closers --publish   # записать, опубликовать, проверить живую страницу
  python3 scripts/na_tildu.py --all --publish              # все страницы services/
  python3 scripts/na_tildu.py --ping                       # связь с MCP (только чтение)

Источник SEO — комментарий «SEO-поля страницы» в шапке файла: Адрес, Title,
Description, H1, Keywords (необязательно), Цены.
Ключ: ~/.config/tilda/mcp_token_artemiy. В вывод, argv и репозиторий не попадает.
"""
import argparse
import base64
import datetime
import hashlib
import html
import json
import pathlib
import posixpath
import re
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = "https://teyes-sibir.ru"
HUB = SITE + "/services"
PROJECT = 10257003
T123 = 131                                   # tplid блока «HTML-код» (T123)
MCP_URL = "https://tilda.ru/api/mcp/"
TOKEN_FILE = pathlib.Path.home() / ".config/tilda/mcp_token_artemiy"
PAGES_FILE = ROOT / "scripts/tilda_pages.json"
IMAGES_FILE = ROOT / "scripts/tilda_images.json"
FONT = '@import url("https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap");\n'
SETTINGS_KEYS = ("alias comment customlink date descr fb_appid fb_descr fb_title fb_url folderid label "
                 "link_canonical meta_descr meta_keywords meta_title shorttitle sort tag title twitter_site "
                 "viewpassword writing_direction").split()


class Stop(Exception):
    """Ошибка, после которой страницу публиковать нельзя."""


# ---------- MCP Tilda по HTTP ----------

class Mcp:
    def __init__(self):
        if not TOKEN_FILE.exists():
            raise Stop(f"нет ключа {TOKEN_FILE}")
        self.token = TOKEN_FILE.read_text().strip()
        self.session = None
        self.proto = "2025-06-18"
        self.n = 0
        r = self._post({"method": "initialize", "params": {
            "protocolVersion": self.proto, "capabilities": {},
            "clientInfo": {"name": "teyes-na-tildu", "version": "1"}}})
        self.proto = r.get("result", {}).get("protocolVersion", self.proto)
        self._post({"method": "notifications/initialized"}, notify=True)

    def _post(self, msg, notify=False):
        msg = {"jsonrpc": "2.0", **msg}
        if not notify:
            self.n += 1
            msg["id"] = self.n
        with tempfile.TemporaryDirectory() as d:
            body, hdr, dump = (pathlib.Path(d, x) for x in ("b.json", "h.txt", "r.txt"))
            body.write_text(json.dumps(msg, ensure_ascii=False), encoding="utf-8")
            hdr.write_text(f"Authorization: Bearer {self.token}\nContent-Type: application/json\n"
                           f"Accept: application/json, text/event-stream\nMCP-Protocol-Version: {self.proto}\n"
                           + (f"Mcp-Session-Id: {self.session}\n" if self.session else ""))
            out = subprocess.run(["curl", "-sS", "--max-time", "180", "-X", "POST", MCP_URL,
                                  "-H", f"@{hdr}", "--data-binary", f"@{body}", "-D", str(dump)],
                                 capture_output=True, text=True)
            if out.returncode:
                raise Stop("curl: " + out.stderr.strip())
            status = ""
            for line in dump.read_text(errors="ignore").splitlines():
                if line.lower().startswith("mcp-session-id:"):
                    self.session = line.split(":", 1)[1].strip()
                if line.startswith("HTTP/"):
                    status = line.split()[1] if len(line.split()) > 1 else ""
            if status == "401":
                raise Stop("MCP ответил 401: ключ протух, нужен новый")
            if status and not status.startswith("2"):
                raise Stop(f"MCP ответил HTTP {status}: {out.stdout[:200]}")
            text = out.stdout
        if notify or not text.strip():
            return {}
        try:
            if not text.lstrip().startswith("{"):
                text = [ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")][-1]
            return json.loads(text)
        except (IndexError, ValueError):
            raise Stop(f"непонятный ответ MCP: {text[:200]}")

    def call(self, name, **args):
        r = self._post({"method": "tools/call", "params": {"name": name, "arguments": args}})
        if "error" in r:
            raise Stop(f"{name}: {r['error']}")
        res = r["result"]
        text = "".join(c.get("text", "") for c in res.get("content", []))
        if res.get("isError"):
            raise Stop(f"{name}: {text[:300]}")
        return text

    def json(self, name, **args):
        text = self.call(name, **args)
        try:
            return json.loads(text)
        except ValueError:
            raise Stop(f"{name}: ответ не JSON: {text[:200]}")


# ---------- страницы репозитория ----------

def parse_seo(text):
    m = re.search(r"<!--\s*\n\s*SEO-поля.*?-->", text, re.S)
    if not m:
        raise Stop("нет комментария «SEO-поля страницы» в шапке файла")
    seo = {}
    for line in m.group(0).splitlines():
        k = re.match(r"\s*(Адрес|Title|Description|H1|Keywords|Цены):\s*(.*)$", line)
        if k:
            seo[k.group(1)] = k.group(2).strip()
    seo["Адрес"] = (seo.get("Адрес") or "").split()[0] if seo.get("Адрес") else ""
    for need in ("Адрес", "Title", "Description", "H1"):
        if not seo.get(need):
            raise Stop(f"в SEO-комментарии нет «{need}»")
    if not seo["Адрес"].startswith("/"):
        raise Stop(f"Адрес должен начинаться с /: {seo['Адрес']}")
    return seo


def repo_pages():
    """Каталог страницы в репо → её адрес на сайте."""
    out = {}
    for f in sorted((ROOT / "services").rglob("index.html")):
        rel = f.parent.relative_to(ROOT).as_posix()
        try:
            out[rel] = parse_seo(f.read_text(encoding="utf-8"))["Адрес"]
        except Stop:
            pass
    return out


def plain(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def prepare(page_dir, mcp=None, check_only=False):
    """Готовит код блока T123. Возвращает (code, seo, предупреждения)."""
    text = (ROOT / page_dir / "index.html").read_text(encoding="utf-8")
    seo = parse_seo(text)
    lines = text.split("\n")
    try:
        start = lines.index('<div class="tsb">')
        end = next(i for i, ln in enumerate(lines) if "конец блока T123" in ln)
    except (ValueError, StopIteration):
        raise Stop('не найдены границы блока: строка <div class="tsb"> и метка «конец блока T123»')
    code = "\n".join(lines[start:end]).rstrip()

    code, todos = re.subn(r"\n?[ \t]*<!--(?:(?!-->).)*?TODO(?:(?!-->).)*?-->", "", code, flags=re.S)
    if '<div class="tsb">\n<style>\n' not in code:
        raise Stop("блок должен начинаться с <div class=\"tsb\"> и <style>")
    if FONT not in code:
        code = code.replace('<div class="tsb">\n<style>\n', '<div class="tsb">\n<style>\n' + FONT, 1)

    pages = repo_pages()

    def link(m):
        val = m.group(1)
        if re.match(r"(https?:|tel:|mailto:|#)", val):
            return m.group(0)
        path, _, anchor = val.partition("#")
        target = posixpath.normpath(posixpath.join(page_dir, path)).strip("/")
        if target in ("", "."):
            url = HUB
        elif target in pages:
            url = SITE + pages[target]
        else:
            raise Stop(f"ссылка «{val}» ведёт на {target}, такой страницы в репо нет")
        return f'href="{url}{"#" + anchor if anchor else ""}"'
    code = re.sub(r'href="([^"]*)"', link, code)

    images = load(IMAGES_FILE)
    pending = {}                              # метка → (файл, sha1): загрузить после проверок

    def image(m):
        val = m.group(1)
        if re.match(r"(https?:|data:)", val):
            return m.group(0)
        f = ROOT / posixpath.normpath(posixpath.join(page_dir, val))
        if not f.exists():
            raise Stop(f"нет файла картинки {f.relative_to(ROOT)}")
        key = hashlib.sha1(f.read_bytes()).hexdigest()
        if key in images:
            return f'src="{images[key]}"'
        mark = f"https://upload.pending/{key}"
        pending[mark] = (f, key)
        return f'src="{mark}"'
    code = re.sub(r'src="([^"]*)"', image, code)

    problems = []
    left = re.findall(r'(?:href|src)="(?!https?:|tel:|mailto:|#|data:)[^"]*"', code)
    if left:
        problems.append(f"остались относительные ссылки: {sorted(set(left))}")
    if "TODO" in code:
        problems.append("в коде остался TODO")
    for bad in ("ССЫЛКА_", 'src="ФОТО"'):
        if bad in code:
            problems.append(f"в коде заглушка «{bad}»")
    h1 = re.findall(r"<h1[^>]*>(.*?)</h1>", code, re.S)
    if len(h1) != 1:
        problems.append(f"H1 должен быть ровно один, найдено {len(h1)}")
    elif plain(h1[0]) != plain(seo["H1"]):
        problems.append(f"H1 на странице «{plain(h1[0])}» ≠ H1 в SEO-комментарии «{seo['H1']}»")
    if problems:
        raise Stop("; ".join(problems))

    warn = []
    if todos:
        warn.append(f"вырезано TODO-комментариев: {todos} — на странице не хватает данных, проверь, чего именно")
    if pending and check_only:
        warn.append(f"фото к загрузке на CDN Tilda при записи: {len(pending)}")
    for mark, (f, key) in pending.items():
        if check_only:
            break
        mime = {".png": "image/png", ".webp": "image/webp"}.get(f.suffix.lower(), "image/jpeg")
        r = mcp.json("upload_image", projectid=PROJECT, images=[{
            "image_base64": base64.b64encode(f.read_bytes()).decode(), "filename": f.name, "mimetype": mime}])
        images[key] = r["images"][0]["url"]
        save(IMAGES_FILE, images)
        code = code.replace(mark, images[key])
    # метка версии: по ней проверка живой страницы отличает новую публикацию от старой из кеша
    ver = hashlib.sha1(code.encode()).hexdigest()[:12]
    code = code.replace('<div class="tsb">', f'<div class="tsb" data-tsb-ver="{ver}">', 1)
    seo["ver"] = ver
    if re.search(r"ориентир|подтвердить", seo.get("Цены", ""), re.I):
        warn.append(f"цены — ориентир по рынку, не свой прайс: {seo['Цены']}")
    if not seo.get("Keywords"):
        warn.append("нет Keywords в SEO-комментарии")
    crumb = re.search(r'aria-current="page">([^<]+)<', code)
    seo["short"] = plain(crumb.group(1)) if crumb else seo["H1"]
    return code, seo, warn


# ---------- Tilda ----------

def load(f):
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def save(f, data):
    f.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def find_or_create(mcp, alias, page_dir):
    """Страница и её блок T123 по алиасу. Возвращает (pageid, recordid, создана ли).

    Трогаем только страницы, которые завёл этот скрипт (они в реестре). Чужая страница
    сайта с тем же адресом, сменённый «Адрес» или исчезнувшая страница — СТОП: решает Артём.
    """
    reg = load(PAGES_FILE)
    old = [a for a, v in reg.items() if v.get("src") == page_dir and a != alias]
    if old:
        raise Stop(f"у страницы сменился адрес: на сайте она /{old[0]}, в файле /{alias}. Иначе на сайте "
                   f"останутся обе. Нужен 301 со старого адреса или снятие старой — решает Артём; "
                   f"потом поправить запись в {PAGES_FILE.name}")
    entry = reg.get(alias)
    created = False
    if entry:
        pageid = entry["pageid"]
        try:
            recs = mcp.json("get_page_records", projectid=PROJECT, pageid=pageid)
        except Stop as e:
            raise Stop(f"страница {pageid} из реестра недоступна (удалена?): {e}. Убрать запись /{alias} "
                       f"из {PAGES_FILE.name} — по решению Артёма")
    else:
        pages = mcp.json("get_pages", projectid=PROJECT)
        hit = [p for p in pages if (p.get("alias") or "") == alias]
        if hit:
            raise Stop(f"на сайте уже есть страница /{alias} (id {hit[0]['id']}), её завёл не этот скрипт. "
                       f"Перезаписывать её — только по решению Артёма: тогда внести её в {PAGES_FILE.name}")
        pageid = int(mcp.call("add_blank_page", projectid=PROJECT).strip())
        created = True
        recs = []
    if len(recs) > 1 or (recs and str(recs[0].get("tplid")) != str(T123)):
        raise Stop(f"на странице {pageid} не один блок T123 ({len(recs)} блоков) — её правили руками, решает Артём")
    if recs:
        recordid = int(recs[0]["recordid"])
    else:
        recordid = int(mcp.json("add_record", projectid=PROJECT, pageid=pageid, tplid=T123)["recordid"])
    reg[alias] = {"pageid": pageid, "recordid": recordid, "src": page_dir}
    save(PAGES_FILE, reg)
    return pageid, recordid, created


def write_page(mcp, code, seo, publish, page_dir):
    alias = seo["Адрес"].lstrip("/")
    url = SITE + seo["Адрес"]
    pageid, recordid, created = find_or_create(mcp, alias, page_dir)
    mcp.call("update_record", projectid=PROJECT, pageid=pageid, recordid=recordid,
             tab="content", only_field="code", fields={"code": code})
    back = mcp.json("edit_record", projectid=PROJECT, pageid=pageid, recordid=recordid)["record"].get("code", "")
    if back != code:
        raise Stop(f"код в Tilda не совпал с отправленным ({len(back)} против {len(code)} симв.)")

    cur = mcp.json("get_page_settings", projectid=PROJECT, pageid=pageid)
    cur = cur.get("settings", cur) if isinstance(cur, dict) else {}
    args = {k: cur[k] for k in SETTINGS_KEYS if cur.get(k) not in (None, "") and k not in ("fb_title", "fb_descr", "fb_url")}
    for k in ("sort", "folderid"):
        if k in args:
            try:
                args[k] = int(args[k])
            except (TypeError, ValueError):
                args.pop(k)
    args.update(alias=alias, title=seo["Title"], meta_title=seo["Title"], descr=seo["Description"],
                meta_descr=seo["Description"], link_canonical=url, shorttitle=seo["short"])
    if seo.get("Keywords"):
        args["meta_keywords"] = seo["Keywords"]
    args["date"] = str(args.get("date", ""))[:16]          # Tilda отдаёт с секундами, принимает без
    if not re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", str(args.get("date", ""))):
        args["date"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    mcp.call("update_page_settings", projectid=PROJECT, pageid=pageid, **args)
    if publish:
        mcp.call("publish_page", projectid=PROJECT, pageid=pageid)
    return pageid, created


# ---------- проверка живой страницы ----------

def http(url):
    out = subprocess.run(["curl", "-sS", "-L", "--max-time", "30", "-o", "/dev/null", "-w", "%{http_code}", url],
                         capture_output=True, text=True)
    return out.stdout.strip() or "000"


def check_live(code, seo):
    url = SITE + seo["Адрес"]
    mark = f'data-tsb-ver="{seo["ver"]}"'
    page = ""
    for _ in range(6):                        # публикация доходит до CDN не мгновенно
        page = subprocess.run(["curl", "-sS", "-L", "--max-time", "30", f"{url}?nc={time.time():.0f}"],
                              capture_output=True, text=True).stdout
        if mark in page:
            break
        time.sleep(3)
    else:
        return [f"на живой странице не новая версия (нет метки {seo['ver']}): старая из кеша или публикация не прошла"], 0, 0
    bad = []
    code_http = http(url)
    if code_http != "200":
        bad.append(f"страница отдаёт {code_http}")
    meta = lambda name: (re.search(rf'<meta name="{name}" content="([^"]*)"', page) or [None, None])[1]
    title = plain((re.search(r"<title>(.*?)</title>", page, re.S) or [None, ""])[1])
    if title != plain(seo["Title"]):
        bad.append(f"title «{title}»")
    if plain(meta("description") or "") != plain(seo["Description"]):
        bad.append("description не совпал")
    if seo.get("Keywords") and plain(meta("keywords") or "") != plain(seo["Keywords"]):
        bad.append("keywords не совпали")
    if f'<link rel="canonical" href="{url}"' not in page:
        bad.append("canonical не на себя")
    if "noindex" in (meta("robots") or ""):
        bad.append("meta robots: noindex")
    h1 = re.findall(r"<h1[^>]*>(.*?)</h1>", page, re.S)
    if len(h1) != 1 or plain(h1[0]) != plain(seo["H1"]):
        bad.append(f"H1 на живой странице: {len(h1)} шт.")
    if "FAQPage" in code and "FAQPage" not in page:
        bad.append("нет разметки FAQPage")
    links = sorted({u.split("#")[0] for u in re.findall(r'href="(https://teyes-sibir\.ru/[^"]*)"', code)})
    imgs = sorted(set(re.findall(r'src="(https://[^"]*)"', code)))
    broken = [u for u in links + imgs if http(u) != "200"]
    if broken:
        bad.append(f"битые ссылки/картинки: {broken}")
    return bad, len(links), len(imgs)


# ---------- запуск ----------

def main():
    ap = argparse.ArgumentParser(description="Перенос страниц репозитория на teyes-sibir.ru (Tilda)")
    ap.add_argument("pages", nargs="*", help="каталоги страниц, например services/closers")
    ap.add_argument("--all", action="store_true", help="все страницы services/")
    ap.add_argument("--check", action="store_true", help="только подготовить и проверить, в Tilda не писать")
    ap.add_argument("--publish", action="store_true", help="опубликовать и проверить живую страницу")
    ap.add_argument("--ping", action="store_true", help="проверить связь с MCP Tilda")
    a = ap.parse_args()

    if a.ping:
        t = time.time()
        mcp = Mcp()
        n = len(mcp.json("get_projects"))
        print(f"MCP Tilda: связь есть, проектов {n}, {time.time() - t:.1f} с, протокол {mcp.proto}")
        return 0
    targets = sorted(repo_pages()) if a.all else [p.strip("/") for p in a.pages]
    if not targets:
        ap.error("укажите страницу (services/closers) или --all")

    mcp = None if a.check else Mcp()
    failed = 0
    for page_dir in targets:
        t = time.time()
        print(f"\n=== {page_dir}")
        try:
            code, seo, warn = prepare(page_dir, mcp, check_only=a.check)
            print(f"  адрес:    {SITE}{seo['Адрес']}")
            print(f"  title:    {seo['Title']}")
            print(f"  H1:       {seo['H1']}")
            print(f"  keywords: {seo.get('Keywords') or '—'}")
            print(f"  код:      {len(code)} симв.")
            for w in warn:
                print(f"  ВНИМАНИЕ: {w}")
            if a.check:
                print("  ПРОВЕРКА: ок, в Tilda ничего не записано")
                continue
            pageid, created = write_page(mcp, code, seo, a.publish, page_dir)
            print(f"  Tilda:    страница {pageid}{' (создана)' if created else ''}, код записан и сверен байт в байт")
            if not a.publish:
                print("  ЧЕРНОВИК: не опубликовано, на сайте без изменений")
                continue
            bad, nl, ni = check_live(code, seo)
            if bad:
                failed += 1
                print("  ПРОВАЛ:   " + "; ".join(bad))
            else:
                print(f"  ОПУБЛИКОВАНО и проверено: новая версия {seo['ver']} на сайте, 200, title, description, "
                      f"{'keywords, ' if seo.get('Keywords') else ''}canonical, H1, "
                      f"{nl} ссылок и {ni} картинок отвечают 200 — {time.time() - t:.0f} с")
        except Stop as e:
            failed += 1
            print(f"  СТОП:     {e}")
        except Exception as e:                    # не обрывать --all посреди списка
            failed += 1
            print(f"  СБОЙ:     {type(e).__name__}: {e}")
    print(f"\nИтого: {len(targets) - failed} из {len(targets)} без ошибок")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
