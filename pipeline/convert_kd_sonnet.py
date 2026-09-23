#!/usr/bin/env python3
"""Convert the records of the first Claude/Sonnet pass over the dox library (own prompt and schema, one file per
docid with page-located quotes) into the record shape of extract_prefaces.py, as corpus "kd-dox", so that verify /
resolve_all / merge_all consume them and extract_prefaces.py skips those documents.

  python3 convert_kd_sonnet.py SRC_DIR OCR_DIR      # SRC_DIR: <docid>.json files; OCR_DIR: text cache

Type mapping: the eight shared types unchanged; hosted_by -> position_at (role kept, "visiting"); dedicated_to ->
other (role "dedicatee"); thanked and funded_by are outside the upstream design (thanks are not relations there)
and go to data/kd_sonnet_extra.jsonl for a possible later layer."""
import glob, hashlib, json, os, sys

from extract_relations import DATA, REL_TYPES, quote_ok, _norm

KIND = {"monograph": "monograph", "dissertation": "dissertation", "critical_edition": "text_edition", "translation": "text_edition",
        "festschrift": "festschrift", "collected_papers": "edited_volume", "conference_volume": "edited_volume", "catalogue": "other",
        "reference_work": "other", "article": "article", "journal_issue": "journal_volume", "primary_text_reprint": "text_edition"}


def main():
    src, ocr = sys.argv[1], sys.argv[2]
    out_dir = os.path.join(DATA, "prefaces", "kd-dox")
    os.makedirs(out_dir, exist_ok=True)
    extra = open(os.path.join(DATA, "kd_sonnet_extra.jsonl"), "w", encoding="utf-8")
    n = nr = ne = 0
    for f in sorted(glob.glob(os.path.join(src, "*.json"))):
        d = json.load(open(f))
        if d.get("duplicate_of") or not d.get("document"):
            continue
        m, doc = d["_meta"], d["document"]
        path = os.path.join(ocr, m["relpath"] + ".txt")
        rec = {"author": (doc.get("authors") or [{}])[0].get("name"), "doc_kind": KIND.get(doc.get("genre"), "other"),
               "doc_year": doc.get("year"), "people": [{"name": p["name"], "birth_year": p.get("birth_year"), "death_year": p.get("death_year"),
                                                        "nationality": p.get("nationality"), "field": None} for p in d.get("people", [])],
               "relations": [], "docid": os.path.splitext(os.path.basename(m["relpath"]))[0], "corpus": "kd-dox", "path": path,
               "meta": {"title": doc.get("title"), "author": "; ".join(a["name"] for a in doc.get("authors") or []), "year": doc.get("year"),
                        "language": doc.get("language"), "kd_docid": m["docid"], "category": m.get("category"), "pdf_pages": m.get("pdf_pages"),
                        "dissertation": doc.get("dissertation"), "preface_signed": doc.get("preface_signed")},
               "model": "claude:sonnet (kd pass 1)"}
        for r in d.get("relations", []):
            t, role = r["type"], r.get("role")
            if t in ("thanked", "funded_by"):
                extra.write(json.dumps({**r, "docid": rec["docid"], "path": path}, ensure_ascii=False) + "\n"); ne += 1
                continue
            if t == "hosted_by":
                t, role = "position_at", ("visiting: " + role) if role else "visiting scholar"
            elif t == "dedicated_to":
                t, role = "other", ("dedicatee" + (f" ({role})" if role else ""))
            if t not in REL_TYPES:
                continue
            rec["relations"].append({"subject": r["subject"], "type": t, "object": r["object"], "role": role, "place": r.get("place"),
                                     "year_start": r.get("year_start"), "year_end": r.get("year_end"), "explicit": r.get("explicit", True),
                                     "evidence": r["evidence"], "quote_ok": bool(r.get("quote_ok")), "page": r.get("page_found") or r.get("page"),
                                     "gloss_en": r.get("gloss_en")})
            nr += 1
        h = hashlib.sha1(path.encode()).hexdigest()[:16]     # = extract_prefaces.out_path for the worklist record of this path
        json.dump(rec, open(os.path.join(out_dir, h + ".json"), "w"), ensure_ascii=False, indent=1); n += 1
    print(f"[convert] {n} records, {nr} relations kept, {ne} thanked/funded_by set aside")


if __name__ == "__main__":
    main()
