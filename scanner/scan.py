#!/usr/bin/env python3
"""Scan school websites near Cuneo for English-teaching contract work.

Standard library only, so it runs on a free GitHub Actions runner with no installs and no AI.

    python3 scanner/scan.py            # full scan, writes site/jobs.json and data/state.json
    python3 scanner/scan.py --only CNIC85900A,CNPS03000D   # test a few sources

Each school site is read with the reader that fits its platform:
  argo       public albo RSS     generale.portaleargo.it/albopretorio/api/public/atti/rss/SCxxxxx
  spaggiari  albo search page    <site>/albo-online?cerca=inglese
  nuvola     public noticeboard  nuvola.madisoft.it/bacheca-digitale/bacheca/<code>/<n>/IN_PUBBLICAZIONE/0/show
  karon      albo + circolari    <site>/albo-online, <site>/feed/circolari.rss
  wordpress  site search feed    <site>/?s=inglese&feed=rss2
  links      any other page: the links on it (and on its job/notice pages) that look like postings
"""
import datetime as dt
import hashlib
import html
import http.client
import json
import os
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import match  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHOOLS = os.path.join(ROOT, "data", "schools.json")
EXTRA = os.path.join(ROOT, "data", "extra_sources.json")
STATE = os.path.join(ROOT, "data", "state.json")
BACKUP = os.path.join(ROOT, "data", "her_sites_backup.json")
OUT = os.path.join(ROOT, "docs", "jobs.json")
SOURCES_API = os.environ.get("SOURCES_API", "").rstrip("/")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/141.0 Safari/537.36 CuneoEnglishJobs/1.0")
SEARCH_WORDS = ["inglese", "madrelingua", "lettorato"]
KEEP_DAYS = 120          # forget notices older than this
TODAY = dt.date.today()
CTX = ssl.create_default_context()
CTX.check_hostname = False   # several school sites have broken certificate chains
CTX.verify_mode = ssl.CERT_NONE


# ---------------------------------------------------------------- fetching

# Many school sites share a few hosting servers (Spaggiari, Argo, Nuvola). Those answer
# "509 / 429" when one visitor asks too fast, so requests are throttled per server IP:
# at most 2 at a time, at least 0.7 s apart, with backoff and retries on a refusal.
_ip_cache, _ip_locks, _ip_last = {}, {}, {}
_guard = threading.Lock()
PER_SERVER = 2
GAP = 0.7


def _server_of(url):
    host = urllib.parse.urlparse(url).hostname or ""
    with _guard:
        if host not in _ip_cache:
            try:
                _ip_cache[host] = socket.gethostbyname(host)
            except OSError:
                _ip_cache[host] = host
        ip = _ip_cache[host]
        if ip not in _ip_locks:
            _ip_locks[ip] = threading.BoundedSemaphore(PER_SERVER)
            _ip_last[ip] = 0.0
        return ip


RELAY = SOURCES_API            # the same Cloudflare Worker, path /fetch
SCAN_KEY = os.environ.get("SCAN_KEY", "")
REFUSED = {202, 403, 429, 509}  # rate limits and bot checks seen on school hosting
_relay_servers = set()          # servers that refused us directly: go through the relay from then on


def _direct(url, max_bytes, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9,en;q=0.5"})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        try:
            raw = r.read(max_bytes)
        except http.client.IncompleteRead as e:
            raw = e.partial
        return r.status, r.geturl(), raw.decode(r.headers.get_content_charset() or "utf-8", "replace")


def _via_relay(url, max_bytes, timeout):
    req = urllib.request.Request(f"{RELAY}/fetch?url={urllib.parse.quote(url, safe='')}",
                                 headers={"User-Agent": UA, "X-Scan-Key": SCAN_KEY})
    try:
        with urllib.request.urlopen(req, timeout=timeout + 15) as r:
            raw = r.read(max_bytes)
            status = int(r.headers.get("X-Upstream-Status") or 0)
            return status, r.headers.get("X-Upstream-Url") or url, raw.decode(r.headers.get_content_charset() or "utf-8", "replace")
    except urllib.error.HTTPError as e:
        return int(e.headers.get("X-Upstream-Status") or e.code), url, ""
    except Exception as e:
        return 0, url, type(e).__name__


def fetch(url, max_bytes=3_000_000, timeout=25):
    """GET a URL. Returns (status, final_url, text). Never raises.

    Direct first; if the server refuses (rate limit or bot check), once more through the Worker.
    """
    server = _server_of(url)
    use_relay = bool(RELAY and SCAN_KEY)
    if use_relay and server in _relay_servers:
        return _via_relay(url, max_bytes, timeout)
    result = (0, url, "")
    for attempt in range(2):
        with _ip_locks[server]:
            with _guard:
                pause = _ip_last[server] + GAP - time.monotonic()
                _ip_last[server] = max(time.monotonic(), _ip_last[server] + GAP)
            if pause > 0:
                time.sleep(pause)
            try:
                result = _direct(url, max_bytes, timeout)
                if result[0] not in REFUSED:
                    return result
            except urllib.error.HTTPError as e:
                result = (e.code, url, "")
                if e.code in (502, 503, 504) and attempt == 0:
                    time.sleep(3)
                    continue
            except Exception as e:  # DNS, TLS, timeouts
                result = (0, url, type(e).__name__)
                if attempt == 0 and not isinstance(getattr(e, "reason", None), socket.gaierror):
                    time.sleep(2)
                    continue
        break
    if use_relay and (result[0] in REFUSED or (result[0] == 0 and result[2] in ("TimeoutError", "URLError", "RemoteDisconnected"))):
        relayed = _via_relay(url, max_bytes, timeout)
        if relayed[0] == 200:
            if result[0] in REFUSED:
                with _guard:
                    _relay_servers.add(server)
            return relayed
    return result


def text_of(fragment):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment or "")).split())


def links_in(page, base):
    body = re.sub(r"(?is)<(script|style|svg)\b.*?</\1>", " ", page)
    for href, inner in re.findall(r'(?is)<a\b[^>]*?href\s*=\s*["\']([^"\'#][^"\']*)["\'][^>]*>(.*?)</a>', body):
        label = text_of(inner)
        if not label:
            m = re.search(r'title\s*=\s*["\']([^"\']+)', inner)
            label = html.unescape(m.group(1)) if m else ""
        yield urllib.parse.urljoin(base, html.unescape(href.strip())), label


def rss_items(xml):
    out = []
    for block in re.findall(r"(?is)<(?:item|entry)\b.*?</(?:item|entry)>", xml):
        def tag(name):
            m = re.search(rf"(?is)<{name}\b[^>]*>(.*?)</{name}>", block)
            if not m:
                return ""
            v = m.group(1).strip()
            v = re.sub(r"(?s)^<!\[CDATA\[(.*)\]\]>$", r"\1", v)
            return v
        link = tag("link") or (re.search(r'<link[^>]*href="([^"]+)"', block) or [None, ""])[1]
        out.append({
            "title": text_of(tag("title")),
            "url": html.unescape(link.strip()),
            "date": match.parse_date(tag("pubDate") or tag("dc:date") or tag("published") or tag("updated")),
            "text": text_of(tag("description") or tag("content:encoded") or tag("summary"))[:4000],
        })
    return out


# ---------------------------------------------------------------- platform readers

def read_argo(code):
    st, _, xml = fetch(f"https://generale.portaleargo.it/albopretorio/api/public/atti/rss/{code}")
    if st != 200:
        return None
    return rss_items(xml)


def read_spaggiari(base):
    items, ok = [], False
    for word in SEARCH_WORDS:
        st, final, page = fetch(f"{base}/albo-online?categoria=&cerca={word}&storico=&aoo=")
        if st != 200 or "at-item" not in page:
            continue
        ok = True
        for block in re.split(r'<div class="media at-item', page)[1:]:
            title = text_of((re.search(r'(?is)<h3 class="media-heading[^"]*">(.*?)</h3>', block) or [None, ""])[1])
            date = match.parse_date((re.search(r"(?is)Pubblicato il:\s*</label>\s*<strong>([^<]+)", block) or [None, ""])[1])
            link = re.search(r'href="([^"]+)"', block)
            url = urllib.parse.urljoin(final, html.unescape(link.group(1))) if link else f"{base}/albo-online?cerca={word}"
            items.append({"title": title, "url": url, "date": date, "text": ""})
    return items if ok else None


def read_nuvola(code, sections):
    items, ok = [], False
    for sec in sections[:4]:
        st, final, page = fetch(f"https://nuvola.madisoft.it/bacheca-digitale/bacheca/{code}/{sec}/IN_PUBBLICAZIONE/0/show")
        if st != 200:
            continue
        ok = True
        for row in re.findall(r'(?is)<tr data-index=.*?</tr>', page):
            title = text_of((re.search(r'(?is)grid-column-documentoRegistro_documento_oggetto[^>]*>(.*?)</td>', row) or [None, ""])[1])
            date = match.parse_date(text_of((re.search(r'(?is)grid-column-dataPubblicazioneOnline[^>]*>(.*?)</td>', row) or [None, ""])[1]))
            link = re.search(r'href="([^"]+/documento/[^"]+)"', row)
            items.append({"title": title, "url": html.unescape(link.group(1)) if link else final, "date": date, "text": ""})
    return items if ok else None


def read_karon(base):
    items, ok = [], False
    for path in ("/albo-online", "/albo-online?page=2"):
        st, final, page = fetch(base + path)
        if st != 200:
            continue
        ok = True
        for row in re.findall(r'(?is)<tr class="tr-albo-online".*?</tr>', page):
            cells = re.findall(r"(?is)<td>(.*?)</td>", row)
            link = re.search(r'href="([^"]+)"[^>]*title="([^"]*)"', row)
            if not link:
                continue
            dates = match.find_dates(text_of(cells[1]) if len(cells) > 1 else "")
            items.append({"title": html.unescape(link.group(2)), "url": urllib.parse.urljoin(final, link.group(1)),
                          "date": dates[0] if dates else None, "text": ""})
    for feed in ("/feed/circolari.rss", "/feed/notizie.rss", "/feed/comunicati.rss"):
        st, _, xml = fetch(base + feed)
        if st == 200 and "<item" in xml:
            ok = True
            items += rss_items(xml)
    return items if ok else None


def read_wordpress(base):
    items, ok = [], False
    for word in SEARCH_WORDS:
        st, _, xml = fetch(f"{base}/?s={word}&feed=rss2")
        if st == 200 and ("<rss" in xml[:3000] or "<channel" in xml[:3000]):
            ok = True
            items += rss_items(xml)
    return items if ok else None


HUB = re.compile(r"lavora\s+con\s+noi|offerte\s+di\s+lavoro|careers?|jobs?\b|recruit|reclutament|bandi|avvisi|"
                 r"albo|selezion|collabora|news|notizie|novit|comunicat|annunci", re.I)


def read_links(base, home):
    """Generic reader: links on the page and on up to 5 'notice-like' pages it links to."""
    host = urllib.parse.urlparse(base).netloc
    items, hubs = [], []
    for url, label in links_in(home, base):
        items.append({"title": label, "url": url, "date": match.date_from_url(url), "text": ""})
        if HUB.search(label) and urllib.parse.urlparse(url).netloc == host and url not in hubs:
            hubs.append(url)
    for hub in hubs[:5]:
        st, final, page = fetch(hub)
        if st == 200:
            for url, label in links_in(page, final):
                items.append({"title": label, "url": url, "date": match.date_from_url(url), "text": ""})
    return items


# ---------------------------------------------------------------- per-source scan

MANUAL_ALBO = [
    (re.compile(r"trasparenzascuole\.it/Public/APDPublic", re.I), "Axios albo (not readable automatically)"),
    (re.compile(r"web\.spaggiari\.eu/sdg/[^\"']*albo", re.I), "Spaggiari albo (blocks automatic reading)"),
    (re.compile(r"sogiscuola\.com", re.I), "Sogiscuola albo (not readable automatically)"),
]


def manual_links(home, base):
    out = []
    for url, label in links_in(home, base):
        for rx, why in MANUAL_ALBO:
            if rx.search(url) and url not in [o["url"] for o in out]:
                out.append({"url": url, "why": why})
    return out[:2]


def scan_source(src):
    res = {"id": src["id"], "status": "ok", "readers": [], "checked": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "items": [], "manual": []}
    if not src.get("site"):
        res["status"] = "no-site"
        return res
    st, final, home = fetch(src["site"])
    if st != 200:
        res["status"] = "unreachable"
        res["error"] = f"HTTP {st}" if st else (home or "no answer")[:60]
        return res
    base = re.match(r"https?://[^/]+", final).group(0)
    candidates = []

    argo = re.search(r"(?:customerCode=|codcli=|albipretorionline\.com/)(SC\d{3,})", home, re.I)
    if argo:
        got = read_argo(argo.group(1).upper())
        if got is not None:
            res["readers"].append("argo")
            candidates += got
    nuv = re.findall(r"bacheca-digitale/bacheca/([A-Z0-9]{10})/(\d+)", home)
    if nuv:
        code = nuv[0][0]
        secs = sorted({int(s) for c, s in nuv if c == code})
        got = read_nuvola(code, secs)
        if got is not None:
            res["readers"].append("nuvola")
            candidates += got
    if re.search(r"scaling\.spaggiari\.eu|custcode=pvw|/pvw2/", home):
        got = read_spaggiari(base)
        if got is not None:
            res["readers"].append("spaggiari")
            candidates += got
    if re.search(r'name="application-name"\s+content="SCUOLASTICO"', home, re.I):
        got = read_karon(base)
        if got is not None:
            res["readers"].append("karon")
            candidates += got
    if "wp-content" in home:
        got = read_wordpress(base)
        if got is not None:
            res["readers"].append("wordpress")
            candidates += got
    # Always also look at the links on the page itself (catches news posts and pages she adds by hand).
    candidates += read_links(final, home) if (src.get("added") or not res["readers"]) else list(
        {"title": l, "url": u, "date": match.date_from_url(u), "text": ""} for u, l in links_in(home, final))
    res["readers"].append("links")
    res["manual"] = manual_links(home, final)

    seen = set()
    for c in candidates:
        key = c["url"] + "|" + c["title"][:80]
        if key in seen or not c["title"]:
            continue
        seen.add(key)
        kind = match.classify(c["title"], c.get("text", ""))
        if not kind:
            continue
        if c["date"] and c["date"] < TODAY - dt.timedelta(days=KEEP_DAYS):
            continue
        res["items"].append({
            "title": match.clean_title(c["title"])[:300],
            "url": c["url"],
            "kind": kind,
            "date": c["date"].isoformat() if c["date"] else None,
            "deadline": (lambda d: d.isoformat() if d else None)(match.find_deadline(c.get("text", ""), TODAY)),
        })
    # A "decreto di avvio" is posted next to the avviso itself; keep only the avviso when both are there.
    start_decree = re.compile(r"decreto\s+(di|per)\s+(l['’]\s*)?avvio", re.I)
    avvisi = [dt.date.fromisoformat(i["date"]) for i in res["items"]
              if i["kind"] == "open" and i["date"] and not start_decree.search(i["title"])
              and re.search(r"\b(avviso|bando|manifestazione)\b", i["title"], re.I)]
    res["items"] = [i for i in res["items"] if not (
        start_decree.search(i["title"]) and i["date"]
        and any(abs((dt.date.fromisoformat(i["date"]) - a).days) <= 3 for a in avvisi))]
    return res


# ---------------------------------------------------------------- sources, state, output

def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def source_id(url):
    host = urllib.parse.urlparse(url if "://" in url else "https://" + url).netloc.lower().removeprefix("www.")
    return "web-" + hashlib.sha1(host.encode()).hexdigest()[:8]


def load_sources():
    sources = []
    for s in load_json(SCHOOLS, []):
        sources.append({"id": s["code"], "name": s["name"], "town": s["town"], "minutes": s["minutes"],
                        "site": s["site"], "kind": s["kind"]})
    for s in load_json(EXTRA, []):
        sources.append({"id": s.get("id") or source_id(s["site"]), "kind": "other", "added": True, **s})
    removed = set()
    if SOURCES_API:
        st, _, body = fetch(SOURCES_API + "/sources", timeout=20)
        if st == 200:
            try:
                live = json.loads(body)
                removed = set(live.get("removed", []))
                # Backup: each run commits a copy of her list, so git history keeps every version.
                with open(BACKUP, "w", encoding="utf-8") as f:
                    json.dump({"added": sorted(live.get("added", []), key=lambda a: a.get("addedAt", "")),
                               "removed": sorted(removed)}, f, ensure_ascii=False, indent=1)
                known = {s["site"].rstrip("/") for s in sources if s.get("site")}
                for a in live.get("added", []):
                    if a["url"].rstrip("/") not in known:
                        sources.append({"id": a["id"], "name": a.get("name") or urllib.parse.urlparse(a["url"]).netloc,
                                        "town": a.get("town") or "", "minutes": None, "site": a["url"], "kind": "added",
                                        "added": True})
            except (ValueError, KeyError):
                print("warning: could not read the sources list from", SOURCES_API, file=sys.stderr)
        else:
            print(f"warning: sources list unavailable (HTTP {st}); scanning the built-in list only", file=sys.stderr)
    return sources, removed


def item_id(url, title):
    return hashlib.sha1(f"{url}|{title[:80]}".encode()).hexdigest()[:12]


def main():
    only = None
    if "--only" in sys.argv:
        only = set(sys.argv[sys.argv.index("--only") + 1].split(","))
    sources, removed = load_sources()
    todo = [s for s in sources if s["id"] not in removed and (not only or s["id"] in only)]
    started = time.time()
    with ThreadPoolExecutor(max_workers=12) as ex:
        results = {r["id"]: r for r in ex.map(scan_source, todo)}

    state = load_json(STATE, {"items": {}, "sources": {}})
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    for src in todo:
        r = results[src["id"]]
        prev = state["sources"].get(src["id"], {})
        # Undated links found on a site's first successful read are its baseline (old), not news.
        first_scan = not prev.get("lastOk")
        state["sources"][src["id"]] = {
            "status": r["status"], "checked": r["checked"], "readers": r["readers"], "manual": r["manual"],
            "error": r.get("error"),
            "lastOk": r["checked"] if r["status"] == "ok" else prev.get("lastOk"),
        }
        for it in r["items"]:
            iid = item_id(it["url"], it["title"])
            old = state["items"].get(iid)
            if old:
                old.update({k: v for k, v in it.items() if v})
                old["lastSeen"] = now
            else:
                state["items"][iid] = {**it, "source": src["id"], "firstSeen": now, "lastSeen": now,
                                       "baseline": first_scan and not it["date"]}

    # forget old notices
    cutoff = (TODAY - dt.timedelta(days=KEEP_DAYS)).isoformat()
    state["items"] = {k: v for k, v in state["items"].items()
                      if (v.get("date") or v["firstSeen"][:10]) >= cutoff}

    by_id = {s["id"]: s for s in sources}
    jobs = []
    for iid, v in state["items"].items():
        if v["source"] in removed or v["source"] not in by_id or v.get("baseline"):
            continue
        kind = match.classify(v["title"])  # re-check saved notices against the current rules
        if not kind:
            continue
        v["kind"] = kind
        jobs.append({"id": iid, "title": v["title"], "url": v["url"], "kind": kind, "source": v["source"],
                     "date": v.get("date"), "deadline": v.get("deadline"), "firstSeen": v["firstSeen"]})
    jobs.sort(key=lambda j: (j["date"] or j["firstSeen"][:10]), reverse=True)

    out_sources = []
    for s in sources:
        st = state["sources"].get(s["id"], {})
        out_sources.append({
            "id": s["id"], "name": s["name"], "town": s.get("town"), "minutes": s.get("minutes"), "site": s.get("site"),
            "kind": s["kind"], "status": "removed" if s["id"] in removed else st.get("status", "waiting"),
            "checked": st.get("checked"), "readers": [r for r in st.get("readers", []) if r != "links"],
            "manual": st.get("manual", []),
        })

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"generated": now, "sources": out_sources, "jobs": jobs}, f, ensure_ascii=False, indent=1)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)

    ok = sum(1 for r in results.values() if r["status"] == "ok")
    print(f"scanned {len(todo)} sources in {time.time() - started:.0f}s: {ok} ok, "
          f"{len(todo) - ok} unreachable; {sum(1 for j in jobs if j['kind'] == 'open')} open notices, "
          f"{sum(1 for j in jobs if j['kind'] == 'filled')} recently filled")


if __name__ == "__main__":
    main()
