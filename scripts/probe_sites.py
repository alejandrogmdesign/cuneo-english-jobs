"""One-off: probe each school site to learn its platform (WordPress JSON, RSS, albo provider)."""
import json, re, sys, ssl, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
UA = {'User-Agent': 'Mozilla/5.0 (compatible; CuneoEnglishJobs/0.1; job list for a teacher)'}
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
def get(url, n=400000):
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20, context=CTX)
        return r.status, r.geturl(), r.read(n).decode('utf-8', 'replace'), r.headers.get('content-type', '')
    except urllib.error.HTTPError as e: return e.code, url, '', ''
    except Exception as e: return 0, url, type(e).__name__, ''
PROVIDERS = {'spaggiari': r'spaggiari\.eu', 'argo': r'argo(soft|\.it|albo)|portaleargo', 'axios': r'axios(italia)?\.(it|com)|albo.?axios',
             'nuvola': r'madisoft|nuvola\.', 'trasparenza-pa': r'trasparenza-pa\.net', 'scuoladigitale': r'scuoladigitale|scuolainterattiva',
             'segreteria-digitale': r'segreteriadigitale', 'gruppo-spaggiari-albo': r'albo-?online'}
def probe(s):
    out = {'code': s['code'], 'site': s['site']}
    if not s['site']: return out
    st, final, html, ct = get(s['site'])
    out.update(status=st, final=final)
    if st != 200: out['err'] = html[:40]; return out
    base = re.match(r'https?://[^/]+', final).group(0)
    out['wp'] = 'wp-content' in html
    gen = re.search(r'<meta name="generator" content="([^"]+)"', html, re.I); out['generator'] = gen.group(1)[:40] if gen else None
    out['providers'] = [k for k, p in PROVIDERS.items() if re.search(p, html, re.I)]
    out['interpelli_link'] = bool(re.search(r'interpell', html, re.I))
    out['madrelingua'] = bool(re.search(r'madrelingua', html, re.I))
    if out['wp']:
        st2, _, body, ct2 = get(base + '/wp-json/wp/v2/posts?per_page=3&_fields=id,date,title,link')
        out['wpjson'] = st2 == 200 and body.lstrip().startswith('[')
    st3, _, body3, ct3 = get(base + '/feed/', 50000)
    out['rss'] = st3 == 200 and ('<rss' in body3[:2000] or '<feed' in body3[:2000])
    return out
schools = json.load(open(sys.argv[1]))
with ThreadPoolExecutor(16) as ex: res = list(ex.map(probe, schools))
json.dump(res, open(sys.argv[2], 'w'), indent=1)
