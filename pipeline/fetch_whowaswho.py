#!/usr/bin/env python3
"""Klaus Karttunen's 'Who Was Who in Indology' (whowaswho-indology.info, ~4,300 biographical entries) as a corpus.

Entries are compact dictionary articles: birth and death with place and date, studies, teachers ("his teachers were
X and Y at Helsinki"), posts with years, students ("Among his students were ..."), then publications and sources.
We fetch every entry listed in the site's sitemap (politely, ~1 request/s), keep the article text locally
(data/whowaswho/, not committed), and write a worklist for extract_prefaces.py (corpus "whowaswho"); the publications
list is cut off to save tokens. The header line is parsed for birth/death year so that the dates count as text-sourced.
Every link cites the entry: "Klaus Karttunen, Who Was Who in Indology: NAME".
"""
import html, json, os, re, sys, time
import requests

from extract_relations import DATA

UA = {"User-Agent": "indology-genealogy/0.2 (academic genealogy research; https://github.com/dharmamitra/indology-genealogy)"}
OUT = os.path.join(DATA, "whowaswho")
SITE = "https://whowaswho-indology.info"


def get(url, tries=6):
    for a in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=60)
            if r.status_code == 200:
                time.sleep(0.8)
                return r.text
            if r.status_code == 404:
                return ""
            time.sleep(int(r.headers.get("retry-after") or 15) if r.status_code == 429 else 8 * (a + 1))
        except requests.RequestException:
            time.sleep(10)
    return None


def article_text(page):
    m = re.search(r"(?is)<article.*?</article>", page) or re.search(r"(?is)<main.*?</main>", page)
    body = m.group(0) if m else page
    body = re.sub(r"(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", body)
    body = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h\d)>", "\n", body)
    text = html.unescape(re.sub(r"<[^>]+>", " ", body))
    text = re.sub(r"[ \t]+", " ", re.sub(r"­", "", text))  # soft hyphens
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def main():
    os.makedirs(OUT, exist_ok=True)
    urls = []
    for i in (1, 2, 3):
        xml = get(f"{SITE}/wp-sitemap-posts-post-{i}.xml") or ""
        urls += re.findall(r"<loc>([^<]+)</loc>", xml)
    print(f"[wwwi] {len(urls)} entries in the sitemap", flush=True)
    n = 0
    with open(os.path.join(DATA, "worklist_whowaswho.jsonl"), "w", encoding="utf-8") as out:
        for k, url in enumerate(urls):
            slug = url.rstrip("/").split("/")[-1]
            pid = url.rstrip("/").split("/")[-2]
            path = os.path.join(OUT, f"{pid}-{slug}.txt")
            if os.path.exists(path):
                text = open(path, encoding="utf-8").read()
            else:
                page = get(url)
                if not page:
                    continue
                text = article_text(page)
                open(path, "w", encoding="utf-8").write(text)
            # " AALTO, Pentti\n 2017-01-25 2026-08-03 \n AALTO, Pentti . Pori 22.7.1917 — Helsinki 30.11.1998. Finnish ..."
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            name = re.sub(r"\s*[–-]\s*Persons of Indian Studies.*$", "", lines[0]).strip(" .") if lines else slug
            body = "\n".join(l for l in lines[1:] if not re.fullmatch(r"\d{4}-\d\d-\d\d(\s+\d{4}-\d\d-\d\d)?", l))
            body = re.split(r"\n\s*(Publications?|Bibliography|Sources?)\s*:", body, maxsplit=1)[0]
            m = re.search(r"(?<!\d)(1[5-9]\d\d|20[0-2]\d)\s*[—–-]+\s*[^\n]{0,80}?(?<!\d)(1[5-9]\d\d|20[0-2]\d)(?!\d)", body[:400])
            birth = int(m.group(1)) if m else None
            death = int(m.group(2)) if m else None
            if not m:
                m1 = re.search(r"(?:\*|b\.|born)\s*[^.\n]{0,60}?(\d{4})", body[:300])
                birth = int(m1.group(1)) if m1 else None
            if len(body) < 120:
                continue
            rec = {"docid": f"Who Was Who in Indology: {name}", "path": os.path.abspath(path), "corpus": "whowaswho",
                   "meta": {"title": f"Who Was Who in Indology: {name}", "author": "Klaus Karttunen", "year": None, "language": "en", "url": url,
                            "subject": name, "birth_year": birth, "death_year": death},
                   "head": f"Biographical dictionary entry on {name} (born {birth or '?'}, died {death or '?'}) from Klaus Karttunen's Who Was Who in Indology. " + body[:300].replace("\n", " "),
                   "windows": [{"where": "full", "start": 0, "score": 99, "text": body[:12000]}]}
            out.write(json.dumps(rec, ensure_ascii=False) + "\n"); n += 1
            if k % 200 == 0:
                print(f"[wwwi] {k}/{len(urls)}", flush=True)
    print(f"[wwwi] {n} records written")


if __name__ == "__main__":
    main()
