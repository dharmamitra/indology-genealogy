#!/usr/bin/env python3
"""The computational leg of the field: papers on Sanskrit / Pali / Tibetan / Buddhist NLP and digital philology are
published in ACL venues and on arXiv, not in the Indological corpora. Fetch them (open access), take their text layer,
and build a worklist of acknowledgement windows for extract_prefaces.py.

  data/computational/acl/<id>.pdf|.txt, data/computational/arxiv/<id>.pdf|.txt   (local only)
  data/worklist_computational.jsonl  -> extract_prefaces.py --worklist ... --outdir data/prefaces (corpus "computational")
"""
import gzip, io, json, os, re, time
import requests

from extract_relations import DATA
from build_worklist import windows_of

UA = {"User-Agent": "indology-genealogy/0.2 (https://github.com/dharmamitra/indology-genealogy)"}
OUT = os.path.join(DATA, "computational")
TOPIC = re.compile(r"\b(Sanskrit|P[aā]li\b|Prakrit|Vedic|Veda\b|Tibetan|Buddhis|Devanagari|Devan[aā]gar[iī]|Indo-Aryan|Classical Indian|"
                   r"Mah[aā]bh[aā]rata|R[aā]m[aā]ya[nṇ]a|P[aā][nṇ]ini|sandhi|Kharo[sṣ][tṭ]h[iī]|Br[aā]hm[iī]|Gandh[aā]r|Nepali manuscript|"
                   r"Indic (?:language|script)|Hindi|Marathi|Bengali|Tamil|Kannada|Telugu|Malayalam)", re.I)
STRICT = re.compile(r"\b(Sanskrit|P[aā]li\b|Prakrit|Vedic|Tibetan|Buddhis|Devanagari|P[aā][nṇ]ini|Mah[aā]bh[aā]rata|Kharo[sṣ][tṭ]h|Br[aā]hm[iī]|Gandh[aā]r)", re.I)


def get(url, binary=False, tries=6, pause=3.0):
    for a in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=120)
            if r.status_code == 200 and not (not binary and "Rate exceeded" in r.text[:200]):
                time.sleep(pause)
                return r.content if binary else r.text
            if r.status_code == 200:  # arXiv answers a burst with an HTTP 200 "Rate exceeded" page
                time.sleep(30 * (a + 1)); continue
            time.sleep(int(r.headers.get("retry-after") or 20) if r.status_code == 429 else 10 * (a + 1))
        except requests.RequestException:
            time.sleep(10)
    return None


def pdf_text(path):
    import fitz
    try:
        doc = fitz.open(path)
        return "\n".join(p.get_text() for p in doc)
    except Exception:
        return ""


def acl():
    """ACL Anthology: every paper whose title matches the topic (bib dump is ~13 MB gzipped)."""
    d = os.path.join(OUT, "acl"); os.makedirs(d, exist_ok=True)
    bib = os.path.join(OUT, "anthology.bib")
    if not os.path.exists(bib):
        raw = get("https://aclanthology.org/anthology.bib.gz", binary=True)
        open(bib, "wb").write(gzip.decompress(raw))
    text = open(bib, encoding="utf-8", errors="ignore").read()
    hits = []
    for m in re.finditer(r"@\w+\{([^,]+),(.*?)\n\}", text, re.S):
        key, body = m.group(1), m.group(2)
        t = re.search(r"title\s*=\s*[\"{](.+?)[\"}],?\n", body, re.S)
        title = re.sub(r"\s+", " ", re.sub(r"[{}]", "", t.group(1))).strip() if t else ""  # {S}anskrit -> Sanskrit
        url = re.search(r"url\s*=\s*[\"{](.+?)[\"}]", body)
        book = re.search(r"booktitle\s*=\s*[\"{](.+?)[\"}],?\n", body, re.S)
        book = re.sub(r"[{}]", "", book.group(1)) if book else ""
        if (STRICT.search(title) or STRICT.search(book)) and url and "@proceedings" not in m.group(0)[:14]:
            y = re.search(r"year\s*=\s*[\"{](\d{4})", body)
            hits.append((key, title, url.group(1), int(y.group(1)) if y else None))
    print(f"[comp] ACL Anthology: {len(hits)} papers", flush=True)
    for key, title, url, year in hits:
        pdf = os.path.join(d, key.replace("/", "_") + ".pdf")
        if not os.path.exists(pdf):
            data = get(url.rstrip("/") + ".pdf", binary=True, pause=1.5)
            if not data:
                continue
            open(pdf, "wb").write(data)
        yield key, title, year, pdf


def arxiv():
    """Semantic Scholar bulk search (computer-science papers on the topic) -> their open-access PDFs (mostly arXiv / ACL).
    The arXiv API itself rate-limits this host."""
    d = os.path.join(OUT, "arxiv"); os.makedirs(d, exist_ok=True)
    seen, token = set(), None
    for q in ("Sanskrit", "Pali language", "Tibetan language", "Buddhist texts", "Devanagari", "Vedic", "Prakrit", "Panini grammar"):
        token = None
        while True:
            params = {"query": q, "fields": "title,year,openAccessPdf,externalIds,fieldsOfStudy", "fieldsOfStudy": "Computer Science,Linguistics", "limit": 1000}
            if token:
                params["token"] = token
            js = get("https://api.semanticscholar.org/graph/v1/paper/search/bulk?" + requests.compat.urlencode(params), pause=1.5)
            if not js:
                break
            js = json.loads(js)
            for e in js.get("data", []):
                title = e.get("title") or ""
                pdfurl = (e.get("openAccessPdf") or {}).get("url")
                arx = (e.get("externalIds") or {}).get("ArXiv")
                if not STRICT.search(title) or e["paperId"] in seen or not (pdfurl or arx):
                    continue
                seen.add(e["paperId"])
                pdf = os.path.join(d, e["paperId"] + ".pdf")
                if not os.path.exists(pdf):
                    data = get(f"https://arxiv.org/pdf/{arx}" if arx else pdfurl, binary=True, pause=3.5 if arx else 1.0, tries=3)
                    if not data or not data.startswith(b"%PDF"):
                        continue
                    open(pdf, "wb").write(data)
                yield e["paperId"], title, e.get("year"), pdf
            token = js.get("token")
            if not token:
                break
    print(f"[comp] Semantic Scholar: {len(seen)} papers", flush=True)


def main():
    n = 0
    with open(os.path.join(DATA, "worklist_computational.jsonl"), "w", encoding="utf-8") as out:
        for src, gen in (("acl", acl()), ("arxiv", arxiv())):
            for key, title, year, pdf in gen:
                txt = pdf[:-4] + ".txt"
                if not os.path.exists(txt):
                    open(txt, "w", encoding="utf-8").write(pdf_text(pdf))
                text = open(txt, encoding="utf-8").read()
                if len(text) < 3000:
                    continue
                wins = windows_of(text)
                if not wins:  # short papers: the acknowledgement window may score below the threshold
                    m = re.search(r"Acknowledg", text)
                    if not m:
                        continue
                    wins = [{"where": "back", "start": m.start(), "score": 5, "text": text[max(0, m.start() - 200):m.start() + 4000]}]
                out.write(json.dumps({"docid": f"{src}:{key}", "path": os.path.abspath(txt), "corpus": "computational",
                                      "meta": {"title": title, "author": "", "year": year, "language": "en"},
                                      "head": re.sub(r"\s+", " ", text[:700]), "windows": wins[:2]}, ensure_ascii=False) + "\n")
                n += 1
    print(f"[comp] {n} records written")


if __name__ == "__main__":
    main()
