#!/usr/bin/env python3
"""Fetch the Wikipedia articles (de / en / ja) of all scholars matched to Wikidata and turn them into a worklist for
extract_prefaces.py.  The method based on prefaces only learns "A taught B" when B's own book is in the corpus; encyclopaedia
biographies close that gap ("studierte bei ...", "Zu seinen Schülern zählen ...").

  data/wikipedia/<lang>/<qid>.txt        article text (local only; CC BY-SA, not committed)
  data/worklist_wikipedia.jsonl          -> extract_prefaces.py --worklist ... --outdir data/wikipedia_rel --min-score 0
"""
import json, os, re, threading, time
from concurrent.futures import ThreadPoolExecutor

import requests

from extract_relations import DATA, ROOT

UA = {"User-Agent": "indology-genealogy/0.2 (https://github.com/dharmamitra/indology-genealogy)"}
OUT = os.path.join(DATA, "wikipedia")
WIN = 8000
_locks = {l: threading.Lock() for l in ("de", "en", "ja")}


def fetch(lang, title, qid):
    path = os.path.join(OUT, lang, qid + ".txt")
    if os.path.exists(path):
        return open(path, encoding="utf-8").read()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for attempt in range(8):
        try:
            with _locks[lang]:  # one request at a time per language edition, ~1.5/s
                time.sleep(0.7)
                r = requests.get(f"https://{lang}.wikipedia.org/w/api.php", headers=UA, timeout=60, params=dict(
                    action="query", prop="extracts", explaintext=1, exsectionformat="plain", redirects=1, titles=title, format="json"))
                if r.status_code == 429:
                    time.sleep(int(r.headers.get("retry-after") or 30) + 2)
            if r.status_code == 200:
                pages = r.json().get("query", {}).get("pages", {})
                text = next(iter(pages.values()), {}).get("extract", "") if pages else ""
                open(path, "w", encoding="utf-8").write(text)
                return text
        except (requests.RequestException, ValueError):
            time.sleep(5)
    return ""


def main():
    g = json.load(open(os.path.join(ROOT, "docs", "data", "graph.json")))
    jobs = []
    for n in g["nodes"]:
        if n["type"] != "person" or not n.get("qid"):
            continue
        for lang, key in (("de", "dewiki"), ("en", "enwiki"), ("ja", "jawiki")):
            if n.get(key):
                jobs.append((lang, n[key], n["qid"], n["label"]))
    print(f"[wikipedia] {len(jobs)} articles", flush=True)
    with ThreadPoolExecutor(3) as ex:
        texts = list(ex.map(lambda j: fetch(j[0], j[1], j[2]), jobs))
    n = 0
    with open(os.path.join(DATA, "worklist_wikipedia.jsonl"), "w", encoding="utf-8") as out:
        for (lang, title, qid, label), text in zip(jobs, texts):
            text = re.split(r"\n(?:Literatur|Schriften|Werke|Weblinks|Einzelnachweise|References|Bibliography|Selected works|Publications|External links|Works|著書|著作|脚注|参考文献|外部リンク)\s*\n", text)[0]
            if len(text) < 300:
                continue
            wins = [{"where": "wikipedia", "start": s, "score": 99, "text": text[s:s + WIN]} for s in range(0, min(len(text), 3 * WIN), WIN)]
            for i in range(0, len(wins), 2):
                out.write(json.dumps({"docid": f"Wikipedia ({lang}): {title}", "path": f"wikipedia/{lang}/{qid}.txt#{i // 2}", "corpus": "wikipedia-" + lang,
                                      "meta": {"title": f"Wikipedia ({lang}): {title}", "author": "", "year": None, "language": lang},
                                      "head": f"Wikipedia biography of {label}. " + text[:400].replace("\n", " "), "windows": wins[i:i + 2]}, ensure_ascii=False) + "\n")
                n += 1
    print(f"[wikipedia] {n} records written")


if __name__ == "__main__":
    main()
