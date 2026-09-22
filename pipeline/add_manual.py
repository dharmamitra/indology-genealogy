#!/usr/bin/env python3
"""Editorial additions: facts that the sources in the corpora do not state, contributed by people who know them.

data/manual/relations.tsv  (tab-separated, one relation per line; lines starting with # are ignored)
  subject	type	object	role	year_start	year_end	source	contributor
  Karin Preisendanz	student_of	Albrecht Wezler	doctoral supervisor	1985	 	Preisendanz, Studien zu Nyāyasūtra III.1 (1994), Vorwort	S. Nehrdich 2026-09
The "source" is a citation of where the fact can be checked; it is shown with the link like an evidence quote.
This script turns the table into data/prefaces/manual/manual.json, which the resolve/merge steps read like any other
extraction (the verifier is bypassed for this corpus).
"""
import csv, json, os
from extract_relations import DATA, REL_TYPES

src = os.path.join(DATA, "manual", "relations.tsv")
rows = []
with open(src, encoding="utf-8") as f:
    for r in csv.DictReader((l for l in f if l.strip() and not l.startswith("#")), delimiter="\t"):
        if r["type"] not in REL_TYPES or not r["subject"].strip() or not r["object"].strip():
            print("skipped:", r); continue
        rows.append({"subject": r["subject"].strip(), "type": r["type"], "object": r["object"].strip(), "role": r.get("role") or None,
                     "place": None, "year_start": int(r["year_start"]) if (r.get("year_start") or "").strip() else None,
                     "year_end": int(r["year_end"]) if (r.get("year_end") or "").strip() else None, "explicit": True,
                     "evidence": f'[editorial addition, {r.get("contributor", "").strip()}] {r["source"].strip()}', "quote_ok": True})
out = os.path.join(DATA, "prefaces", "manual"); os.makedirs(out, exist_ok=True)
json.dump({"author": None, "doc_kind": "other", "doc_year": None, "people": [], "relations": rows, "docid": "Editorial additions",
           "corpus": "manual", "path": src, "meta": {"title": "Editorial additions", "author": "", "year": None}}, open(os.path.join(out, "manual.json"), "w"), ensure_ascii=False, indent=1)
print(len(rows), "editorial relations written")
