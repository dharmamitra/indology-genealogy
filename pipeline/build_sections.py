#!/usr/bin/env python3
"""Second worklist: biography-dense sections anywhere in a document, plus a few books parsed in full.

Obituaries inside journal volumes (JRAS "Obituary Notices", BEFEO "Nécrologie", JAOS "In memoriam"), 略歴 of
Festschrift honorees, biographical sketches in anthologies ... are found by counting phrases that are specific to
modern scholarly careers in 8k-character buckets. Only buckets above a threshold go to Gemini (at most MAX_WIN per
document), so a 2 MB journal volume costs a handful of calls instead of 150.

Histories of the field are taken whole (FULL).  Output: data/worklist_sections.jsonl (same shape as the preface worklist,
two windows per record; consumed by extract_prefaces.py --worklist ... --outdir data/sections).
"""
import glob, json, os, re, sys, unicodedata
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

from extract_relations import DATA
from build_worklist import CORPORA, SKIP_LANG, meta_for

WIN, THRESH, MAX_WIN = 8_000, 5, 8

# substrings of file names that are parsed completely
FULL = [
    "Akira Yuyama 2000 Eugène Burnouf", "Rosane Rocher and Ludo Rocher - The Making of Western Indology_ Henry Thomas Colebrooke and the East India Company-Routledge (2012).",
    "JAOS_019-1_1897=pt1journalofameric19ameruoft_bw", "AM8-1932-1", "Old905__Oldenberg_Vedaforschung",
    "img20240609_15552423", "Yuyama_湯山明_1993@エジャトン",
    "Brief History of Buddhist Studies in Europe and America_J. M. de Jong, 1987", "Jackson_A History of Tibetan Studies",
    "CURATORS of the BUDDHA", "British discovery of Buddhism_Philip C. Almond", "Cabezón_The changing field of Buddhist Studies",
    "Dreyfus_Are We Prisoners of Shangrila", "Kapstein_L’oubli des Russes", "Kuo_In memoriam_Rolf Alfred Stein", "Bareau_Obituary",
]

BIO = re.compile(
    r"was born (?:in|at|on)|was educated at|educated at|was appointed|studied (?:under|with)|a pupil of|pupil of Prof|student of Prof"
    r"|(?:his|her) (?:Ph\.? ?D|doctorate|doctoral)|took (?:his|her) (?:degree|doctorate)|Professor of (?:Sanskrit|Pali|Tibetan|Buddhis|Indian|Indo|Oriental|Chinese|Comparative|Indology|Religio)"
    r"|[Cc]hair of (?:Sanskrit|Pali|Tibetan|Buddhis|Indian|Indo|Oriental|Chinese)|Obituary|OBITUARY|In [Mm]emoriam|IN MEMORIAM|passed away|died (?:on|at|in) "
    r"|wurde .{0,40}geboren|geboren am|habilitierte|promovierte|Privatdozent|Extraordinarius|Ordinarius|wurde .{0,40}berufen|Lehrstuhl|Nachruf|Nekrolog|studierte (?:in|an|bei)|Schüler von"
    r"|né (?:le|à|en) |N[ÉEé]CROLOGIE|Nécrologie|fut nommé|élève de|chaire d[eu']|agrégé|soutint sa thèse|directeur d'études"
    r"|に生まれ|に生る|卒業|に師事|教授に就任|助教授|名誉教授|留学|略歴|略歷|年譜|逝去|学位を|博士号")


def scan(args):
    corpus, path = args
    try:
        meta = meta_for(path)
        if meta.get("language") in SKIP_LANG:
            return None
        text = open(path, encoding="utf-8", errors="ignore").read()
        base = os.path.basename(path)
        nb = unicodedata.normalize("NFC", base)
        full = any(unicodedata.normalize("NFC", f) in nb for f in FULL)
        if full:
            starts = list(range(0, len(text), WIN))
            scored = [(99, s) for s in starts]
        else:
            buckets = defaultdict(int)
            for m in BIO.finditer(text):
                buckets[m.start() // (WIN // 2)] += 1  # half-window buckets, a window = two adjacent halves
            cand = sorted(((buckets[b] + buckets.get(b + 1, 0), b) for b in buckets), reverse=True)
            scored, used = [], set()
            for sc, b in cand:
                if sc < THRESH or len(scored) == MAX_WIN:
                    break
                if b in used or b - 1 in used or b + 1 in used:
                    continue
                used.add(b)
                scored.append((sc, max(0, b * (WIN // 2) - 300)))
        if not scored:
            return None
        wins = [{"where": "full" if full else "section", "start": s, "score": sc, "text": text[s:s + WIN]} for sc, s in sorted(scored, key=lambda x: x[1])]
        return {"docid": base[:-4], "path": path, "corpus": corpus, "meta": meta, "head": re.sub(r"\s+", " ", text[:700]), "windows": wins}
    except Exception as e:
        return {"error": f"{path}: {e!r}"}


def main():
    pref = defaultdict(list)  # windows the preface pass already covers
    wl = os.path.join(DATA, "worklist_prefaces.jsonl")
    if os.path.exists(wl):
        for line in open(wl, encoding="utf-8"):
            r = json.loads(line)
            pref[r["path"]] = [w["start"] for w in r["windows"]]
    tasks = [(c, p) for c, d in CORPORA.items() for p in sorted(glob.glob(os.path.join(d, "*.txt")))]
    seen, nrec, nwin = set(), 0, 0
    out = open(os.path.join(DATA, "worklist_sections.jsonl"), "w", encoding="utf-8")
    with ProcessPoolExecutor(12) as ex:
        for r in ex.map(scan, tasks, chunksize=40):
            if not r or "error" in r:
                if r:
                    print(r["error"], file=sys.stderr)
                continue
            keep = []
            for w in r["windows"]:
                if w["where"] == "section" and any(abs(w["start"] - s) < WIN * 0.6 for s in pref.get(r["path"], [])):
                    continue
                h = re.sub(r"[\W_]+", "", w["text"][500:1700].lower())
                if h in seen or len(h) < 200:
                    continue
                seen.add(h); keep.append(w)
            for i in range(0, len(keep), 2):  # two windows per Gemini call
                rec = {**r, "windows": keep[i:i + 2], "path": f'{r["path"]}#{i // 2}'}
                out.write(json.dumps(rec, ensure_ascii=False) + "\n"); nrec += 1; nwin += len(keep[i:i + 2])
    print(f"[sections] {nrec} records, {nwin} windows ({nwin * WIN / 1e6:.0f}M chars)")


if __name__ == "__main__":
    main()
