#!/usr/bin/env python3
"""Recompute quote_ok for the records of a corpus with the current quote_ok (fuzzy fallback), from the worklist's
window texts. No LLM calls.   python3 requote.py data/worklist_kd-dox.jsonl"""
import json, os, sys

from extract_relations import _norm, quote_ok
from extract_prefaces import out_path

n = before = after = 0
for line in open(sys.argv[1], encoding="utf-8"):
    rec = json.loads(line)
    p = out_path(rec)
    if not os.path.exists(p):
        continue
    d = json.load(open(p))
    wins = "\n\n".join(f"--- excerpt {i + 1} ({w['where']} matter) ---\n{w['text']}" for i, w in enumerate(rec["windows"]))
    cn = _norm(wins)
    changed = False
    for r in d["relations"]:
        before += bool(r.get("quote_ok"))
        ok = quote_ok(r["evidence"], cn, wins)
        after += ok
        if ok != r.get("quote_ok"):
            r["quote_ok"] = ok; changed = True
    if changed:
        json.dump(d, open(p, "w"), ensure_ascii=False, indent=1)
    n += 1
print(f"[requote] {n} records: quote_ok {before} -> {after}")
