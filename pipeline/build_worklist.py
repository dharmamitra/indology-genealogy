#!/usr/bin/env python3
"""Find the relation-rich front/back matter of every document in the corpora (no LLM involved).

Prefaces, acknowledgements, Vorworte, Lebensläufe of old dissertations, あとがき ... are where authors say who
taught them, where they studied and who supervised the thesis. For each document we look at the first 80k and
the last 30k characters, locate marker phrases, score an 8k window after each marker by how much
"thanks / Professor / University / 先生" vocabulary it holds, and keep the best (at most two) windows.

Output: data/worklist_prefaces.jsonl  {docid, path, corpus, meta, head, windows: [{where, start, score, text}]}
"""
import glob, hashlib, json, os, re, sys
from concurrent.futures import ProcessPoolExecutor

from extract_relations import DATA

CORPORA = {
    "mj-qnap": os.path.expanduser("~/data/dharmanexus-modern-japanese/ocr-qnap/out"),
    "mj-data": os.path.expanduser("~/data/dharmanexus-modern-japanese/data"),
    "mj-new": os.path.expanduser("~/data/dharmanexus-modern-japanese/ocr-new"),
    "sd-ref": os.path.expanduser("~/code/sanskrit-dating/reference-literature"),
}
HEAD, TAIL, WIN = 80_000, 30_000, 8_000

MARKER = re.compile(
    r"Acknowledge?ments?|\bPreface\b|\bPREFACE\b|\bForeword\b|\bFOREWORD\b"
    r"|my (?:supervisor|teacher|advisor|adviser|guru|mentor|Doktorvater)s?\b"
    r"|under the (?:supervision|guidance|direction) of|doctoral (?:thesis|dissertation)|dissertation (?:was )?(?:submitted|written)"
    r"|\bVorwort\b|\bVORWORT\b|Danksagung|Vorbemerkung|Lebenslauf|LEBENSLAUF|\bVita\b|\bVITA\b|Inaugural-? ?Dissertation|Habilitationsschrift"
    r"|mein(?:em|en|es|e)? (?:hoch)?(?:verehrten? )?(?:Lehrer|Doktorvater)"
    r"|Avant-propos|AVANT-PROPOS|Remerciements|REMERCIEMENTS|\bPr[ée]face\b|\bPRÉFACE\b|mon ma[iî]tre|sous la direction de"
    r"|Prefazione|Ringraziamenti|Premessa"
    r"|あとがき|はしがき|まえがき|はじめに|後記|謝辞|序文|指導教官|指導教授|恩師|学位論文|博士論文|學位論文"
    r"|略歴|略歷|年譜|献呈の辞|刊行の辞|刊行のことば|記念論[集文]|追悼|を偲|著者紹介|執筆者紹介")
SCORE = re.compile(
    r"thank|grateful|gratitude|indebted|Professor|Prof\.|\bDr\.|Universit|teacher|supervis|advis|studied|student of|pupil"
    r"|fellowship|Lehrer|danke|Dank\b|studierte|promovier|Schüler|remerci|reconnaissan|ma[iî]tre|élève|professeur"
    r"|先生|教授|博士|大学|大學|指導|留学|感謝|御礼|お礼|学恩|卒業|助手|講師|就任|退官|師事|生まれ")
SKIP_LANG = {"sa", "bo", "pi", "hi", "zh"}


def meta_for(path):
    j = path[:-4] + ".json"
    if os.path.exists(j):
        try:
            m = json.load(open(j))
            if isinstance(m, dict):
                return {"title": str(m.get("title") or m.get("Title") or "")[:200], "author": str(m.get("author") or "")[:120],
                        "year": m.get("year"), "language": m.get("language")}
        except Exception:
            pass
    return {}


def windows_of(text):
    n = len(text)
    regions = [("front", 0, min(n, HEAD))]
    if n > HEAD + 5_000:
        regions.append(("back", max(HEAD, n - TAIL), n))
    cands = []
    for where, a, b in regions:
        for m in MARKER.finditer(text, a, b):
            s = max(a if where == "back" else 0, m.start() - 200)
            w = text[s:s + WIN]
            sc = len(SCORE.findall(w))
            cands.append((sc, s, where))
    cands.sort(reverse=True)
    out = []
    for sc, s, where in cands:
        if sc < 5 or len(out) == 2:
            break
        if any(abs(s - o["start"]) < WIN for o in out):
            continue
        out.append({"where": where, "start": s, "score": sc, "text": text[s:s + WIN]})
    return out


def process(args):
    corpus, path = args
    try:
        meta = meta_for(path)
        if meta.get("language") in SKIP_LANG:
            return None
        text = open(path, encoding="utf-8", errors="ignore").read()
        if len(text) < 15_000:  # articles rarely have prefaces; obituaries etc. are handled by the section pass
            return None
        wins = windows_of(text)
        if not wins:
            return None
        return {"docid": os.path.basename(path)[:-4], "path": path, "corpus": corpus, "meta": meta,
                "head": re.sub(r"\s+", " ", text[:700]), "windows": wins}
    except Exception as e:
        return {"error": f"{path}: {e!r}"}


def main():
    tasks = [(c, p) for c, d in CORPORA.items() for p in sorted(glob.glob(os.path.join(d, "*.txt")))]
    print(f"[worklist] {len(tasks)} documents", flush=True)
    seen, n, nwin = set(), 0, 0
    out = open(os.path.join(DATA, "worklist_prefaces.jsonl"), "w", encoding="utf-8")
    with ProcessPoolExecutor(12) as ex:
        for r in ex.map(process, tasks, chunksize=50):
            if not r or "error" in r:
                if r:
                    print(r["error"], file=sys.stderr)
                continue
            # the corpora hold many scans of the same book: drop windows already seen
            keep = []
            for w in r["windows"]:
                h = hashlib.sha1(re.sub(r"[\W_]+", "", w["text"][200:1700].lower()).encode()).hexdigest()
                if h not in seen:
                    seen.add(h); keep.append(w)
            if keep:
                r["windows"] = keep
                out.write(json.dumps(r, ensure_ascii=False) + "\n"); n += 1; nwin += len(keep)
    print(f"[worklist] {n} documents with {nwin} windows ({nwin * WIN / 1e6:.0f}M chars max)")


if __name__ == "__main__":
    main()
