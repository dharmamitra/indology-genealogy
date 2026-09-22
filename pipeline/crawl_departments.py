#!/usr/bin/env python3
"""Current faculty and doctoral students of the departments where the field lives, from the departments' own websites.

For each target institution (data/crawl_targets.json — the places with the most recent posts in the graph):
 1. Gemini + Google Search finds the people/staff pages of the relevant department(s): Indology / South Asian studies,
    Buddhist studies, Tibetan studies, and Japanese 仏教学・インド哲学 departments.
 2. The pages (and linked personal pages, one level down, same host) are fetched politely (1 request/s per host).
 3. Gemini reads the page text and lists people: name, position, field, doctoral supervisor / students if stated,
    each with a verbatim snippet from the page as evidence.
Everything is written as records of the "web" corpus (data/prefaces/web/<id>.json); the source label carries the URL and
the crawl date, and each affiliation is an attestation for the crawl year (the site format's "attested"), never a start
or an end date.  Page texts are kept locally under data/web/ (not committed).
"""
import hashlib, html, json, os, re, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

import requests
from google import genai
from google.genai import types

from extract_relations import load_key, DATA, REL_TYPES, _norm, quote_ok

UA = {"User-Agent": "indology-genealogy/0.2 (academic genealogy research; https://github.com/dharmamitra/indology-genealogy)"}
WEB = os.path.join(DATA, "web")
OUT = os.path.join(DATA, "prefaces", "web")
TODAY = time.strftime("%Y-%m-%d")
YEAR = int(TODAY[:4])
_host_locks, _lock = {}, threading.Lock()

FIND_PROMPT = """Find the web pages that list the CURRENT people (faculty / staff / doctoral students / research fellows) of the \
department(s) at {inst}{where} that work on any of: Indology / Sanskrit / South Asian studies, Buddhist studies, \
Tibetan studies, Indian philosophy (in Japan: 印度哲学, 仏教学, インド学, 仏教文化). Prefer the department's own \
"people" / "staff" / "members" / "教員紹介" / "スタッフ" pages over the university-wide directory. Answer with the department name(s) and the full URLs of \
those pages (up to 8 URLs). If the institution has no such department, say so."""
FIND_SCHEMA = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"url": {"type": "STRING"}, "department": {"type": "STRING"}}, "required": ["url"]}}

READ_PROMPT = """Below is the text of a web page ({url}) of {inst} — {dept}, fetched on {today}. List every person on it who is a \
member of this department or institute (faculty, emeriti, lecturers, research fellows, postdocs, doctoral students, \
librarians of the collection). For each person give:
- "name": full name as written (keep the script of the page; if both a Japanese and a romanised form are given, use the \
Japanese form and put the romanised one in "name_latin")
- "position": their title / status as written (e.g. "Professor of Sanskrit", "Doktorandin", "准教授", "PhD student")
- "status": one of "faculty", "emeritus", "postdoc_or_fellow", "doctoral_student", "staff_other"
- "field": a few words on what they work on, if the page says
- "supervisor": name of the doctoral supervisor if the page states it (students' pages often do); else null
- "evidence": a SHORT VERBATIM snippet of the page text that shows this person and their position (max ~150 characters)
Ignore administrative staff, people of other departments, visitors listed only as event speakers, and alumni.
If the page lists nobody, return [].

PAGE TEXT:
{text}"""
READ_SCHEMA = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
    "name": {"type": "STRING"}, "name_latin": {"type": "STRING", "nullable": True}, "position": {"type": "STRING", "nullable": True},
    "status": {"type": "STRING", "enum": ["faculty", "emeritus", "postdoc_or_fellow", "doctoral_student", "staff_other"]},
    "field": {"type": "STRING", "nullable": True}, "supervisor": {"type": "STRING", "nullable": True}, "evidence": {"type": "STRING"}},
    "required": ["name", "status", "evidence"]}}


def gem(client, prompt, schema, search=False):
    cfg = dict(temperature=0.0, max_output_tokens=16384)
    if search:
        cfg["tools"] = [types.Tool(google_search=types.GoogleSearch())]
    else:
        cfg.update(response_mime_type="application/json", response_schema=schema)
    err = None
    for attempt in range(4):
        try:
            r = client.models.generate_content(model="gemini-flash-latest", contents=prompt, config=types.GenerateContentConfig(**cfg))
            t = r.text or ""
            if search and not r.candidates:
                continue
            if search:  # grounded answers are prose: collect the URLs in the text and behind the grounding redirects
                urls = re.findall(r"https?://[^\s)\]>\"']+", t)
                gm = r.candidates[0].grounding_metadata if r.candidates else None
                for ch in (gm.grounding_chunks if gm else None) or []:
                    try:
                        h = requests.head(ch.web.uri, headers=UA, timeout=20, allow_redirects=True)
                        urls.append(h.url)
                    except requests.RequestException:
                        pass
                dept = re.search(r"\*\*([^*]{6,90})\*\*", t)
                return [{"url": u.rstrip(".,;"), "department": dept.group(1) if dept else ""} for u in dict.fromkeys(urls)
                        if not re.search(r"(?i)wikipedia|google\.|youtube|linkedin|academia\.edu|researchgate|\.pdf$", u)]
            return json.loads(t)
        except Exception as e:
            err = e
            time.sleep(5 * (attempt + 1))
    print("[crawl] gemini failed:", repr(err)[:120], prompt[:80].replace("\n", " "), flush=True)
    return []


def fetch(url):
    """Polite GET: one request per second per host; cached under data/web/."""
    h = hashlib.sha1(url.encode()).hexdigest()
    path = os.path.join(WEB, h + ".html")
    if os.path.exists(path):
        return open(path, encoding="utf-8", errors="ignore").read()
    host = urlparse(url).netloc
    with _lock:
        lk = _host_locks.setdefault(host, threading.Lock())
    with lk:
        try:
            r = requests.get(url, headers=UA, timeout=45, allow_redirects=True)
            time.sleep(1.0)
            if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
                return ""
            r.encoding = r.apparent_encoding if r.encoding in (None, "ISO-8859-1") else r.encoding
            os.makedirs(WEB, exist_ok=True)
            open(path, "w", encoding="utf-8").write(r.text)
            return r.text
        except requests.RequestException:
            return ""


def to_text(page):
    page = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>", " ", page)
    page = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h\d|dd|dt|section|article)>", "\n", page)
    text = html.unescape(re.sub(r"<[^>]+>", " ", page))
    return re.sub(r"[ \t　]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def links(page, base):
    out = []
    for m in re.finditer(r'href="([^"#]+)"', page):
        u = urljoin(base, m.group(1))
        if urlparse(u).netloc == urlparse(base).netloc and re.search(r"(?i)person|people|staff|member|profil|mitarbeiter|faculty|student|doktorand|team|prof|kyoin|教員|スタッフ|/~", u):
            out.append(u.split("?")[0])
    return list(dict.fromkeys(out))


def one(client, tgt):
    inst = tgt["label"]
    where = f' ({tgt.get("city") or ""}, {tgt.get("country") or ""})'.replace(" (, )", "")
    pages = gem(client, FIND_PROMPT.format(inst=inst, where=where), FIND_SCHEMA, search=True)
    recs = []
    seen_names = set()
    for pg in pages[:8]:
        url = pg.get("url", "")
        if not url.startswith("http"):
            continue
        dept = pg.get("department") or ""
        page = fetch(url)
        if not page:
            continue
        texts = [(url, to_text(page))]
        for sub in links(page, url)[:40]:  # personal pages one level down
            sp = fetch(sub)
            if sp:
                texts.append((sub, to_text(sp)))
        for u, text in texts:
            if len(text) < 200:
                continue
            people = gem(client, READ_PROMPT.format(url=u, inst=inst, dept=dept, today=TODAY, text=text[:60000]), READ_SCHEMA)
            rels = []
            for p in people:
                if p["status"] == "staff_other" or not quote_ok(p["evidence"], _norm(text)):
                    continue
                nm = re.sub(r"^((Prof|Professor|Dr|Ph\.?D|Univ|Priv|Doz|Mag|M\.A|Dipl|Ass|em|Hon|Ven|Bhikkhu|Geshe|Lama|Rev)\.?[ -]*)+", "", p["name"].strip()).strip()
                nm = re.sub(r",?\s*(M\.A\.|Ph\.?D\.?|Dr\. des\.|B\.A\.)$", "", nm).strip()
                if len(nm) < 3:
                    continue
                role = p.get("position") or {"faculty": "faculty", "emeritus": "Professor emeritus", "postdoc_or_fellow": "research fellow", "doctoral_student": "doctoral student"}[p["status"]]
                typ = "studied_at" if p["status"] == "doctoral_student" else "position_at"
                key = (nm, typ)
                if key in seen_names:
                    continue
                seen_names.add(key)
                rels.append({"subject": nm, "type": typ, "object": inst, "role": role, "place": None, "year_start": None, "year_end": None,
                             "explicit": True, "evidence": p["evidence"], "quote_ok": True, "web_status": p["status"], "web_field": p.get("field"),
                             "name_latin": p.get("name_latin")})
                if p.get("supervisor"):
                    rels.append({"subject": nm, "type": "student_of", "object": p["supervisor"].strip(), "role": "doctoral supervisor", "place": inst,
                                 "year_start": None, "year_end": None, "explicit": True, "evidence": p["evidence"], "quote_ok": True})
            if rels:
                rid = hashlib.sha1(u.encode()).hexdigest()[:16]
                recs.append({"author": None, "doc_kind": "other", "doc_year": YEAR, "people": [{"name": r["subject"], "field": r.get("web_field")} for r in rels if r.get("web_field")],
                             "relations": rels, "docid": f"Web: {inst} — {dept} ({u})", "corpus": "web", "path": u,
                             "meta": {"title": f"{inst}, {dept}: people page, fetched {TODAY}", "author": "", "year": YEAR, "language": ""}, "url": u, "fetched": TODAY})
    os.makedirs(OUT, exist_ok=True)
    for r in recs:
        json.dump(r, open(os.path.join(OUT, hashlib.sha1(r["url"].encode()).hexdigest()[:16] + ".json"), "w"), ensure_ascii=False, indent=1)
    n = sum(len(r["relations"]) for r in recs)
    print(f"[crawl] {inst}: {len(pages)} pages found, {len(recs)} with people, {n} relations", flush=True)
    return n


def main():
    targets = json.load(open(os.path.join(DATA, os.environ.get("CRAWL_TARGETS", "crawl_targets.json"))))
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else len(targets)
    client = genai.Client(api_key=load_key())
    with ThreadPoolExecutor(6) as ex:
        tot = sum(ex.map(lambda t: one(client, t), targets[:limit]))
    print(f"[crawl] {tot} relations from {limit} institutions")


if __name__ == "__main__":
    main()
