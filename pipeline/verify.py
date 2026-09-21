#!/usr/bin/env python3
"""Second, independent pass over EVERY extracted relation: is it really supported, and which years are really stated?

For each relation (data/chunks, data/prefaces, data/sections) we record in data/verdicts.json:
  verdict   ok | reversed | wrong_type | not_supported        (Gemini, judging ONLY the evidence quote)
  type      corrected relation type for wrong_type
  current   true if the quote describes the situation at the time of publication ("X (Hamburg)", "my supervisor Prof. Y of Z"),
            false if it is retrospective ("formerly professor at", "studied at ... in the 1970s", obituaries, histories)
  ys, ye    the years the QUOTE states for the relation (Gemini) — kept only if the literal-year rule agrees
  ys_lit, ye_lit   where the extracted year was found: "quote", "nearby" (within ~800 characters of the quote in the
            source document) or null.  Years that are neither are never used as dates: if they equal the publication
            year they become an attestation ("was there in 2016"), otherwise they are dropped.

Why: the first pass often put the publication year into year_end, or inferred years that the text does not give.
"""
import glob, json, os, re, sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from google import genai

from extract_relations import load_key, DATA, SRC_DIR, _norm, REL_TYPES
from resolve_entities import llm_json

PROMPT = """You are checking a database of academic relations that another model extracted from publications. For each \
item judge ONLY what the evidence quote says (plus who the author of the publication is, to resolve "I"/"my"/私).

verdict:
- "ok": the quote supports the relation as given (subject, type, object).
- "reversed": the quote supports the relation but with subject and object swapped (e.g. "X examined my dissertation" \
recorded as "X student_of me").
- "wrong_type": the two are related as the quote says, but another type fits (give it in "type"): student_of, studied_at, \
position_at, succeeded, collaborated_with, influenced_by, founded, other.
- "not_supported": the quote does not say this (mere thanks, a citation, co-occurrence of names, a different person, or \
student_of claimed from help/comments/examining only). Examiners, committee members and readers are NOT teachers unless \
the quote says they taught or supervised; co-supervisors and advisors are.
current: true if the quote describes the state of affairs at the time of publication (an affiliation given next to a name, \
"my supervisor", "is professor at"); false if it looks back ("formerly", "was", "studied there in", a career described in \
an obituary, biography or history).
year_start / year_end: ONLY years that the quote itself states for this relation (convert Japanese era years). \
A single year of a degree or thesis goes to year_end. Appointment "in 2006" => year_start 2006. If the quote gives no \
year for the relation, null — never use the publication year.
Echo "k" unchanged.

ITEMS:
{items}"""
SCHEMA = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
    "k": {"type": "INTEGER"}, "verdict": {"type": "STRING", "enum": ["ok", "reversed", "wrong_type", "not_supported"]},
    "type": {"type": "STRING", "enum": REL_TYPES, "nullable": True}, "current": {"type": "BOOLEAN"},
    "year_start": {"type": "INTEGER", "nullable": True}, "year_end": {"type": "INTEGER", "nullable": True}},
    "required": ["k", "verdict", "current"]}}

ERA = {"明治": 1867, "明": 1867, "大正": 1911, "大": 1911, "昭和": 1925, "昭": 1925, "平成": 1988, "平": 1988, "令和": 2018, "令": 2018}
KAN = "〇一二三四五六七八九"


def kan(n):
    if n < 10:
        return KAN[n]
    t, o = divmod(n, 10)
    return ("" if t == 1 else KAN[t]) + "十" + (KAN[o] if o else "")


def year_in(y, text):
    """Is year y written in text (Western digits, abbreviated range end, kanji digits or Japanese era year)?"""
    if str(y) in text or "".join(KAN[int(c)] for c in str(y)) in text:
        return True
    if re.search(r"(1[6-9]|20)\d\d\s*[-–—/~〜]\s*" + str(y)[2:] + r"(?!\d)", text):
        return True
    for e, b in ERA.items():
        n = y - b
        if 1 <= n <= 64 and re.search(e + r"\s*(" + str(n) + "|" + kan(n) + ("|元" if n == 1 else "") + r")(?!\d)", text):
            return True
    return False


_docs = {}


def doc_norm(d):
    """Normalised full text of the source document of a record (None if unavailable)."""
    path = d.get("path", "").split("#")[0] or os.path.join(SRC_DIR, d["docid"] + ".txt")
    if not os.path.isabs(path):
        path = os.path.join(DATA, path)
    if path not in _docs:
        try:
            _docs[path] = _norm(open(path, encoding="utf-8", errors="ignore").read())
        except OSError:
            _docs[path] = None
        if len(_docs) > 40:
            _docs.pop(next(iter(_docs)))
    return _docs[path]


def literal(y, quote, d):
    if not y:
        return None
    if year_in(y, quote):
        return "quote"
    full = doc_norm(d)
    if full:
        q = _norm(quote)[:50]
        i = full.find(q) if len(q) >= 12 else -1
        if i >= 0 and year_in(y, full[max(0, i - 700):i + len(_norm(quote)) + 700]):
            return "nearby"
    return None


def main():
    files = sorted(glob.glob(os.path.join(DATA, "chunks", "*", "*.json")) + glob.glob(os.path.join(DATA, "prefaces", "*", "*.json"))
                   + glob.glob(os.path.join(DATA, "sections", "*", "*.json"))
                   + glob.glob(os.path.join(DATA, "wikipedia_rel", "*", "*.json")))
    items, where = [], []
    verdicts = {}
    for f in files:
        d = json.load(open(f))
        rel = os.path.relpath(f, DATA)
        dy = d.get("doc_year") or (d.get("meta") or {}).get("year") or (1917 if d["docid"].startswith("Win917") else None)
        verdicts[rel] = [None] * len(d["relations"])
        for j, r in enumerate(d["relations"]):
            if not r.get("quote_ok"):
                continue
            v = {"ys_lit": literal(r.get("year_start"), r["evidence"], d), "ye_lit": literal(r.get("year_end"), r["evidence"], d), "doc_year": dy}
            verdicts[rel][j] = v
            items.append({"k": len(items), "publication_author": d.get("author"), "publication_year": dy, "kind": d.get("doc_kind"),
                          "subject": r["subject"], "type": r["type"], "object": r["object"], "role": r.get("role"), "quote": r["evidence"]})
            where.append((rel, j))
    print(f"[verify] {len(items)} relations in {len(files)} records", flush=True)
    client = genai.Client(api_key=load_key())
    calls = [items[i:i + 25] for i in range(0, len(items), 25)]

    def one(ch):
        base = ch[0]["k"]
        local = [{**it, "k": it["k"] - base} for it in ch]  # small, stable k values inside a batch
        try:
            return [(base + r["k"], r) for r in llm_json(client, PROMPT.format(items=json.dumps(local, ensure_ascii=False, indent=0)), SCHEMA)
                    if 0 <= r["k"] < len(ch)]
        except Exception as e:
            print("[verify] batch failed:", repr(e)[:150], flush=True)
            return []
    done = 0
    with ThreadPoolExecutor(64) as ex:
        for res in ex.map(one, calls):
            for k, r in res:
                rel, j = where[k]
                verdicts[rel][j].update(verdict=r["verdict"], type=r.get("type"), current=r.get("current"),
                                        ys=r.get("year_start"), ye=r.get("year_end"))
            done += 1
            if done % 100 == 0:
                print(f"[verify] {done}/{len(calls)} batches", flush=True)
    json.dump(verdicts, open(os.path.join(DATA, "verdicts.json"), "w"), ensure_ascii=False)
    c = Counter(v.get("verdict", "unchecked") for vs in verdicts.values() for v in vs if v)
    print("[verify] verdicts:", dict(c))
    yl = Counter((f, v[f]) for vs in verdicts.values() for v in vs if v for f in ("ys_lit", "ye_lit"))
    print("[verify] literal years:", dict(yl))


if __name__ == "__main__":
    main()
