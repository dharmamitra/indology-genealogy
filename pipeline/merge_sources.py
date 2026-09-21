#!/usr/bin/env python3
"""data/graph_books.json + data/wikidata.json -> docs/data/graph.json

1. attach Wikidata ids, life dates, places, portraits, coordinates; merge institutions that share a QID
2. add Wikidata's dated employer / educated-at / student-of statements: fill missing years on book edges,
   add edges the books do not have (sources = ["Wikidata"], no evidence quote)
3. for relations that still have no start year, ask Gemini for the dates from its own knowledge
   (kept only at high/medium confidence, marked ys_src/ye_src = "model")

Every year carries its provenance: ys_src / ye_src in {"text", "wikidata", "model"}.
"""
import argparse, json, os
from collections import defaultdict, Counter

from google import genai

from extract_relations import load_key, DATA, ROOT
from resolve_entities import llm_json

OUT = DATA
SITE_DATA = os.path.join(ROOT, "docs", "data")
DATED_TYPES = ("position_at", "studied_at", "student_of")
QID_ALIAS = {"Q20266330": "I:University of Berlin", "Q28024477": "I:University of Dorpat",
             "Q27923720": "I:University of Moscow", "Q11524659": "I:University of Tokyo",
             "Q20032795": "I:Marienstiftsgymnasium Stettin"}

MODEL_PROMPT = """You are a historian of Indology and Oriental studies. For each scholar below, some career relations \
lack dates. From your own knowledge give, for every relation, the year it began and the year it ended:
- position_at: years the post was held at that institution
- studied_at: years of study there
- student_of: years of study under that teacher
Return an integer year or null. "confidence": "high" if you know the dates from standard biographies, "medium" if \
accurate to within about two years, "low" if you are guessing. Prefer null/low over invention; obscure people will \
often be unknown. Echo "k" unchanged.

{items}"""
MODEL_SCHEMA = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
    "k": {"type": "INTEGER"}, "year_start": {"type": "INTEGER", "nullable": True},
    "year_end": {"type": "INTEGER", "nullable": True},
    "confidence": {"type": "STRING", "enum": ["high", "medium", "low"]}}, "required": ["k", "confidence"]}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-model", action="store_true", help="skip the Gemini date fill")
    args = ap.parse_args()
    g = json.load(open(os.path.join(OUT, "graph_books.json")))
    wd = json.load(open(os.path.join(OUT, "wikidata.json")))
    nodes = {n["id"]: n for n in g["nodes"]}

    # ---- 1. node attributes
    for nid, w in wd["people"].items():
        n = nodes[nid]
        n.update(qid=w["qid"], image=w["image"], enwiki=w["enwiki"], dewiki=w["dewiki"],
                 birth_place=w["birth_place"], death_place=w["death_place"], description=w["description"])
        for k, wk in (("birth_year", "birth"), ("death_year", "death")):
            if w[wk]:
                n[k] = w[wk]
        n["dates_src"] = "wikidata" if w["birth"] or w["death"] else None
    alias = {}  # institution node id -> surviving node id
    by_qid = {}
    for nid, w in wd["institutions"].items():
        n = nodes[nid]
        if w["qid"] in by_qid:
            keep = nodes[by_qid[w["qid"]]]
            keep["variants"] += [v for v in [n["label"]] + n["variants"] if v not in keep["variants"]]
            alias[nid] = keep["id"]; del nodes[nid]
            continue
        by_qid[w["qid"]] = nid
        n.update(qid=w["qid"], lat=w.get("lat"), lon=w.get("lon"), inception=w.get("inception"))
    person_by_qid = {w["qid"]: nid for nid, w in wd["people"].items()}
    for q, nid in QID_ALIAS.items():  # Wikidata splits some universities into historical and modern items
        if nid in nodes:
            by_qid[q] = nid

    # ---- 2. edges
    edges = {}
    for e in g["edges"]:
        e["source"], e["target"] = alias.get(e["source"], e["source"]), alias.get(e["target"], e["target"])
        e["ys_src"] = "text" if e["year_start"] else None
        e["ye_src"] = "text" if e["year_end"] else None
        k = (e["source"], e["type"], e["target"])
        if k in edges:  # two book edges collapsed by an institution merge
            o = edges[k]
            o["evidence"] += e["evidence"]; o["roles"] = sorted(set(o["roles"]) | set(e["roles"]))
            o["sources"] = sorted(set(o["sources"]) | set(e["sources"]))
            for y, s, f in (("year_start", "ys_src", min), ("year_end", "ye_src", max)):
                if e[y]:
                    o[y], o[s] = (f(o[y], e[y]) if o[y] else e[y]), "text"
        else:
            edges[k] = e
    stats = Counter()
    for nid, w in wd["people"].items():
        for s in w["statements"]:
            kind = s["kind"]
            if kind in ("student_of", "teacher_of"):
                other = person_by_qid.get(s["qid"])
                if not other or other == nid:
                    continue
                src, tgt, typ = (nid, other, "student_of") if kind == "student_of" else (other, nid, "student_of")
            else:
                tgt = by_qid.get(s["qid"])
                if not tgt:
                    info = wd["labels"].get(s["qid"])
                    if not info or not info["label"] or info["label"].startswith("Q"):
                        continue
                    tgt = "I:" + info["label"]
                    if tgt not in nodes:
                        nodes[tgt] = {"id": tgt, "type": "institution", "label": info["label"], "city": None, "country": None,
                                      "kind": "other", "variants": [], "qid": s["qid"], "lat": info["lat"], "lon": info["lon"],
                                      "inception": info.get("inception"), "wikidata_only": True}
                    by_qid[s["qid"]] = tgt
                src, typ = nid, kind
            k = (src, typ, tgt)
            e = edges.get(k)
            if not e:
                e = edges[k] = {"source": src, "type": typ, "target": tgt, "evidence": [], "roles": [], "explicit": True,
                                "sources": [], "year_start": None, "year_end": None, "ys_src": None, "ye_src": None}
                stats["new " + typ] += 1
            if "Wikidata" not in e["sources"]:
                e["sources"].append("Wikidata")
            for y, sf, v, f in (("year_start", "ys_src", s["start"], min), ("year_end", "ye_src", s["end"], max)):
                if v and e[sf] != "text":
                    e[y], e[sf] = (f(e[y], v) if e[y] else v), "wikidata"
                    stats["wd " + y] += 1
    print("[merge] wikidata:", dict(stats))

    # ---- 3. model fill for what is still undated
    E = list(edges.values())
    if not args.no_model:
        todo = defaultdict(list)
        for i, e in enumerate(E):
            if e["type"] in DATED_TYPES and not e["year_start"]:
                todo[e["source"]].append(i)
        persons = sorted(todo)
        client = genai.Client(api_key=load_key())
        filled = 0
        for b in range(0, len(persons), 12):
            items = []
            for pid in persons[b:b + 12]:
                p = nodes[pid]
                items.append({"scholar": p["label"], "life": f'{p.get("birth_year") or "?"}-{p.get("death_year") or "?"}',
                              "relations": [{"k": i, "type": E[i]["type"], "object": nodes[E[i]["target"]]["label"],
                                             "role": "; ".join(E[i]["roles"]) or None,
                                             "known_end": E[i]["year_end"]} for i in todo[pid]]})
            for r in llm_json(client, MODEL_PROMPT.format(items=json.dumps(items, ensure_ascii=False, indent=0)), MODEL_SCHEMA):
                if r.get("confidence") == "low" or not (0 <= r["k"] < len(E)):
                    continue
                e = E[r["k"]]
                p = nodes[e["source"]]
                lo, hi = (p.get("birth_year") or 1500) + 10, (p.get("death_year") or 1960)
                for y, sf in (("year_start", "ys_src"), ("year_end", "ye_src")):
                    v = r.get(y)
                    if v and not e[y] and lo <= v <= hi:
                        e[y], e[sf] = v, "model"; filled += 1
        print(f"[merge] model filled {filled} years on {sum(len(v) for v in todo.values())} undated relations")
    for e in E:
        if e["year_start"] and e["year_end"] and e["year_end"] < e["year_start"]:
            e["year_end"], e["ye_src"] = None, None

    used = {e["source"] for e in E} | {e["target"] for e in E}
    out = {"nodes": [n for n in nodes.values() if n["id"] in used], "edges": E}
    for e in out["edges"]:
        e["evidence"] = [{k: v for k, v in ev.items() if k != "chunk"} for ev in e["evidence"][:3]]
    os.makedirs(SITE_DATA, exist_ok=True)
    json.dump(out, open(os.path.join(SITE_DATA, "graph.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    c = defaultdict(Counter)
    for e in E:
        c[e["type"]]["n"] += 1; c[e["type"]][e["ys_src"] or "undated"] += 1
    for t, v in c.items():
        print(f"[merge] {t:18s} {dict(v)}")
    print(f"[merge] {sum(n['type']=='person' for n in out['nodes'])} people, "
          f"{sum(n['type']=='institution' for n in out['nodes'])} institutions, {len(E)} edges")


if __name__ == "__main__":
    main()
