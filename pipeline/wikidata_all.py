#!/usr/bin/env python3
"""Wikidata reconciliation at scale: data/graph_extracted.json -> data/wikidata.json (same shape as before).

People are looked up by exact label through the SPARQL endpoint (40 names x several language tags per query)
instead of one search request per name; candidates are then fetched 50 at a time. A candidate is accepted when
 - it is a human and its life dates agree with ours (±1), or
 - we have no dates and it is the only human candidate with a scholarly description / occupation.
Institutions: label lookup + Gemini choosing among candidates (as in wikidata_enrich.py).
"""
import hashlib, json, os, re, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import requests
from google import genai

from extract_relations import load_key, DATA
from resolve_entities import llm_json
from wikidata_enrich import api, entities, claim_vals, first_year, qid_of, label, coords, year, PROPS, SCHOLARLY, UA, CACHE

SPARQL = "https://query.wikidata.org/sparql"
LANGS = ["en", "de", "fr", "mul"]


def sparql(query):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, "sparql_" + hashlib.sha1(query.encode()).hexdigest() + ".json")
    if os.path.exists(path):
        return json.load(open(path))
    for attempt in range(8):
        try:
            r = requests.post(SPARQL, data={"query": query, "format": "json"}, headers=UA, timeout=90)
            if r.status_code == 200:
                data = r.json()["results"]["bindings"]
                json.dump(data, open(path, "w"), ensure_ascii=False)
                time.sleep(1.0)
                return data
            time.sleep(int(r.headers.get("retry-after") or 10) + 2 if r.status_code == 429 else 5 * (attempt + 1))
        except (requests.RequestException, ValueError):
            time.sleep(5 * (attempt + 1))
    return []


def lit(s, lang):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"@' + lang


def label_lookup(names, langs, human=True):
    """names -> {name: [qid, ...]} by exact rdfs:label / skos:altLabel match."""
    out = defaultdict(list)
    names = sorted(set(names))
    batches = [names[i:i + 40] for i in range(0, len(names), 40)]

    def one(batch):
        vals = " ".join(lit(n, l) for n in batch for l in langs)
        return sparql(f"SELECT ?l ?item (MIN(YEAR(?bd)) AS ?b) WHERE {{ VALUES ?l {{ {vals} }} {{ ?item rdfs:label ?l }} UNION {{ ?item skos:altLabel ?l }} "
                      + ("?item wdt:P31 wd:Q5 . OPTIONAL { ?item wdt:P569 ?bd } " if human else "") + "} GROUP BY ?l ?item LIMIT 4000")
    with ThreadPoolExecutor(4) as ex:  # the query service allows a handful of parallel queries per client
        for k, rows in enumerate(ex.map(one, batches)):
            for b in rows:
                qid = b["item"]["value"].rsplit("/", 1)[1]
                by = int(b["b"]["value"]) if "b" in b and b["b"]["value"].lstrip("-").isdigit() else None
                if qid not in [x[0] for x in out[b["l"]["value"]]]:
                    out[b["l"]["value"]].append((qid, by))
            if k % 25 == 0:
                print(f"[wd] label lookup {k * 40}/{len(names)}", flush=True)
    return out


def main():
    g = json.load(open(os.path.join(DATA, "graph_extracted.json")))
    people = [n for n in g["nodes"] if n["type"] == "person"]
    insts = [n for n in g["nodes"] if n["type"] == "institution"]
    deg = defaultdict(int)
    for e in g["edges"]:
        deg[e["source"]] += 1; deg[e["target"]] += 1

    # ---- people
    lat = label_lookup([n["label"] for n in people], LANGS)
    nat = label_lookup([n["native"] for n in people if n.get("native")], ["ja", "zh", "mul"])
    def shortlist(n):  # common names have hundreds of namesakes: keep them only if the birth year singles some out
        c = list(dict.fromkeys(lat.get(n["label"], []) + nat.get(n.get("native") or "", [])))
        if len(c) > 8:
            c = [x for x in c if n["birth_year"] and x[1] and abs(x[1] - n["birth_year"]) <= 1]
        return [q for q, _ in c][:8]
    pc = {n["id"]: shortlist(n) for n in people}
    # people with several links but no label hit get one search request each
    todo = [n for n in people if not pc[n["id"]] and deg[n["id"]] >= 4]
    print(f"[wd] {sum(1 for v in pc.values() if v)} people with label candidates; searching {len(todo)} more", flush=True)
    for n in todo:
        pc[n["id"]] = [c["id"] for c in api(dict(action="wbsearchentities", search=n["label"], language="en", uselang="en", type="item", limit=5))["search"]]
    ents = entities([q for qs in pc.values() for q in qs])

    def match(n):
        humans = [q for q in pc[n["id"]] if any(qid_of(c) == "Q5" for c in claim_vals(ents.get(q, {}), "P31"))]
        for q in humans:
            e = ents[q]
            b, d = first_year(e, "P569"), first_year(e, "P570")
            ok = [abs(a - x) <= 1 for a, x in ((n["birth_year"], b), (n["death_year"], d)) if a and x]
            if ok and all(ok):
                return q
        if n["birth_year"] or n["death_year"]:
            # our dates may come from the model; a unique scholarly namesake born within 3 years is still accepted
            cand = [q for q in humans if SCHOLARLY.search(" ".join(v["value"] for v in ents[q].get("descriptions", {}).values()))
                    and all(abs(a - x) <= 3 for a, x in ((n["birth_year"], first_year(ents[q], "P569")),) if a and x)
                    and not (n["birth_year"] and not first_year(ents[q], "P569"))]
            return cand[0] if len(cand) == 1 else None
        cand = [q for q in humans if SCHOLARLY.search(" ".join(v["value"] for v in ents[q].get("descriptions", {}).values()))]
        return cand[0] if len(cand) == 1 and len(humans) == 1 else None
    pm = {n["id"]: match(n) for n in people}
    # one QID must not be claimed by two nodes: keep the better-connected one
    byq = defaultdict(list)
    for nid, q in pm.items():
        if q:
            byq[q].append(nid)
    dup = {q: v for q, v in byq.items() if len(v) > 1}
    json.dump(dup, open(os.path.join(DATA, "wikidata_same_qid.json"), "w"), ensure_ascii=False, indent=1)
    print(f"[wd] people matched: {sum(1 for q in pm.values() if q)}/{len(people)}; {len(dup)} QIDs shared by several nodes (merged later)", flush=True)

    # ---- institutions
    il = label_lookup([n["label"] for n in insts] + [v for n in insts for v in n["variants"][:2]], ["en", "de", "fr", "ja", "mul"], human=False)
    ic = {}
    for n in insts:
        qs = list(dict.fromkeys(q for nm in [n["label"]] + n["variants"][:2] for q, _ in il.get(nm, [])))[:8]
        if not qs and deg[n["id"]] >= 3:
            qs = [c["id"] for c in api(dict(action="wbsearchentities", search=n["label"], language="en", uselang="en", type="item", limit=6))["search"]]
        ic[n["id"]] = qs
    ients = entities([q for qs in ic.values() for q in qs], props="labels|descriptions|claims")
    items = [{"id": n["id"], "name": n["label"], "city": n.get("city"), "kind": n.get("kind"),
              "candidates": [{"qid": q, "label": label(ients.get(q, {})), "description": ients.get(q, {}).get("descriptions", {}).get("en", {}).get("value")} for q in ic[n["id"]]]}
             for n in insts if ic[n["id"]]]
    client = genai.Client(api_key=load_key())
    schema = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"id": {"type": "STRING"}, "qid": {"type": "STRING", "nullable": True}}, "required": ["id"]}}
    chunks = [items[i:i + 50] for i in range(0, len(items), 50)]

    def pick(ch):
        try:
            return llm_json(client, "For each institution pick the Wikidata candidate that is the same institution (the university, "
                            "academy, monastery or institute itself — not a city, building, faculty or person). Return qid null if none fits.\n\n"
                            + json.dumps(ch, ensure_ascii=False), schema)
        except Exception as e:
            print("[wd] institution batch failed", repr(e)[:120]); return []
    with ThreadPoolExecutor(8) as ex:
        res = [r for rs in ex.map(pick, chunks) for r in rs]
    im = {r["id"]: r["qid"] for r in res if r.get("qid") in set(ic.get(r["id"], []))}
    print(f"[wd] institutions matched: {len(im)}/{len(insts)}", flush=True)

    # ---- statements
    ref_qids, P = set(), {}
    for nid, q in pm.items():
        if not q:
            continue
        e, st = ents[q], []
        for prop, kind in PROPS.items():
            for c in claim_vals(e, prop):
                qual = c.get("qualifiers", {})
                qy = lambda p: next((y for y in (year(s) for s in qual.get(p, [])) if y), None)
                o = qid_of(c)
                if o:
                    ref_qids.add(o)
                    st.append({"prop": prop, "kind": kind, "qid": o, "start": qy("P580") or qy("P585"), "end": qy("P582")})
        img = next((c["mainsnak"]["datavalue"]["value"] for c in claim_vals(e, "P18")), None)
        places = {p: next((qid_of(c) for c in claim_vals(e, p)), None) for p in ("P19", "P20")}
        ref_qids.update(v for v in places.values() if v)
        sl = e.get("sitelinks", {})
        P[nid] = {"qid": q, "birth": first_year(e, "P569"), "death": first_year(e, "P570"), "birth_place": places["P19"],
                  "death_place": places["P20"], "image": img, "enwiki": sl.get("enwiki", {}).get("title"),
                  "dewiki": sl.get("dewiki", {}).get("title"), "jawiki": sl.get("jawiki", {}).get("title"),
                  "description": e.get("descriptions", {}).get("en", {}).get("value"), "statements": st}
    ref_qids.update(im.values())
    refs = entities(ref_qids, props="claims|labels")
    labels = {}
    for q, e in refs.items():
        la, lo = coords(e)
        labels[q] = {"label": label(e), "lat": la, "lon": lo, "inception": first_year(e, "P571")}
    need = {q: next((qid_of(c) for p in ("P159", "P131", "P276") for c in claim_vals(refs[q], p)), None)
            for q, v in labels.items() if v["lat"] is None and q in refs}
    loc = entities([v for v in need.values() if v], props="claims|labels")
    for q, l in need.items():
        if l and l in loc:
            labels[q]["lat"], labels[q]["lon"] = coords(loc[l])
    for p in P.values():
        for k in ("birth_place", "death_place"):
            p[k] = labels.get(p[k], {}).get("label") if p[k] else None
        for s in p["statements"]:
            s["label"] = labels.get(s["qid"], {}).get("label")
    I = {nid: {"qid": q, **labels.get(q, {})} for nid, q in im.items()}
    json.dump({"people": P, "institutions": I, "labels": labels}, open(os.path.join(DATA, "wikidata.json"), "w"), ensure_ascii=False, indent=1)
    ns = sum(len(p["statements"]) for p in P.values())
    print(f"[wd] {ns} statements ({sum(1 for p in P.values() for s in p['statements'] if s['start'] or s['end'])} dated), "
          f"{sum(1 for p in P.values() if p['image'])} portraits, {sum(1 for v in I.values() if v.get('lat') is not None)} institutions with coordinates")


if __name__ == "__main__":
    main()
