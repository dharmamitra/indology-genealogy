#!/usr/bin/env python3
"""How good are the model-recalled years? Hold-out test: take relations whose start year IS stated in a publication,
hide the year, ask Gemini exactly as merge_all.py does, and compare. Writes data/model_date_eval.json."""
import json, os, random, statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

try:
    from google import genai
    from google.genai import types
except ImportError:  # only needed for the Gemini backend; see llm.py
    genai = types = None

from extract_relations import load_key, DATA, ROOT
import llm
from resolve_entities import llm_json
from merge_sources import MODEL_PROMPT, MODEL_SCHEMA

g = json.load(open(os.path.join(ROOT, "docs", "data", "graph.json")))
N, E = g["nodes"], g["edges"]
random.seed(11)
pool = [i for i, e in enumerate(E) if e["type"] in ("position_at", "studied_at", "student_of") and e.get("ys_src") == "text"
        and (N[e["s"]].get("qid") or not N[e["s"]].get("dates_model")) and N[e["s"]].get("birth_year")]
sample = random.sample(pool, min(900, len(pool)))
by = defaultdict(list)
for i in sample:
    by[E[i]["s"]].append(i)
persons = sorted(by)
calls = [persons[b:b + 12] for b in range(0, len(persons), 12)]
client = llm.make_client()


def ask(ch):
    items = [{"scholar": N[p]["label"], "native_name": N[p].get("native"), "life": f'{N[p].get("birth_year") or "?"}-{N[p].get("death_year") or "?"}',
              "relations": [{"k": i, "type": E[i]["type"], "object": N[E[i]["t"]]["label"], "role": "; ".join(E[i].get("roles") or []) or None,
                             "known_end": None, "attested_in_publications_of": None, "evaluation": True} for i in by[p]]} for p in ch]
    try:
        return llm_json(client, MODEL_PROMPT.format(items=json.dumps(items, ensure_ascii=False, indent=0)), MODEL_SCHEMA)
    except Exception:
        return []


res = {}
with ThreadPoolExecutor(24) as ex:
    for out in ex.map(ask, calls):
        for r in out:
            res[r["k"]] = r
rows = []
for i in sample:
    r = res.get(i)
    if not r or r.get("confidence") == "low" or not r.get("year_start"):
        continue
    rows.append({"who": N[E[i]["s"]]["label"], "type": E[i]["type"], "at": N[E[i]["t"]]["label"], "truth": E[i]["year_start"],
                 "model": r["year_start"], "confidence": r["confidence"], "wikidata": bool(N[E[i]["s"]].get("qid"))})


def stats(rs):
    err = [abs(x["truth"] - x["model"]) for x in rs]
    return {"n": len(rs), "exact": round(sum(e == 0 for e in err) / len(err), 3), "within_1": round(sum(e <= 1 for e in err) / len(err), 3),
            "within_2": round(sum(e <= 2 for e in err) / len(err), 3), "within_5": round(sum(e <= 5 for e in err) / len(err), 3),
            "off_by_more_than_10": round(sum(e > 10 for e in err) / len(err), 3), "median_abs_error": statistics.median(err)} if err else {"n": 0}


out = {"asked": len(sample), "answered_high_or_medium": len(rows), "all": stats(rows),
       "confidence_high": stats([x for x in rows if x["confidence"] == "high"]), "confidence_medium": stats([x for x in rows if x["confidence"] == "medium"]),
       "with_wikidata_id": stats([x for x in rows if x["wikidata"]]), "without_wikidata_id": stats([x for x in rows if not x["wikidata"]]),
       "by_type": {t: stats([x for x in rows if x["type"] == t]) for t in ("position_at", "studied_at", "student_of")},
       "worst": sorted(rows, key=lambda x: -abs(x["truth"] - x["model"]))[:25]}
json.dump(out, open(os.path.join(DATA, "model_date_eval.json"), "w"), ensure_ascii=False, indent=1)
print(json.dumps({k: v for k, v in out.items() if k != "worst"}, indent=1))
for x in out["worst"][:8]:
    print(x)
