#!/usr/bin/env python3
"""Reconcile the graph's people and institutions with Wikidata and pull temporal data.

  data/graph_books.json -> data/wikidata.json
    people:       node id -> {qid, birth, death, birth_place, death_place, image, enwiki, dewiki, description,
                              statements: [{prop, qid, label, start, end}]}
    institutions: node id -> {qid, label, lat, lon, inception}
    labels:       qid -> {label, lat, lon}   (for employers/schools that are not yet in the graph)

People are matched by name search + life-date agreement (rule based); institutions by name search with
Gemini picking among the candidates. All HTTP answers are cached in data/wd_cache/.
"""
import hashlib, json, os, re, time, threading
from concurrent.futures import ThreadPoolExecutor

import requests
try:
    from google import genai
    from google.genai import types
except ImportError:  # only needed for the Gemini backend; see llm.py
    genai = types = None

from extract_relations import load_key, DATA
import llm
from resolve_entities import llm_json

OUT = DATA
CACHE = os.path.join(DATA, "wd_cache")  # raw API answers, git-ignored
API = "https://www.wikidata.org/w/api.php"
UA = {"User-Agent": "indology-genealogy/0.1 (https://github.com/dharmamitra/indology-genealogy)"}
PROPS = {"P1066": "student_of", "P184": "student_of", "P802": "teacher_of", "P185": "teacher_of",
         "P108": "position_at", "P69": "studied_at"}
SCHOLARLY = re.compile(r"indolog|orientalis|sanskrit|linguist|philolog|missionar|scholar|professor|historian|"
                       r"buddholog|iranist|theolog|civil servant|archaeolog|epigraph|writer|poet|philosoph|"
                       r"jesuit|judge|pandit|translator|tibetolog|sinolog|librarian|numismat|officer|administrator", re.I)
_lock = threading.Lock()
_rate = threading.Lock()


def api(params):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest() + ".json")
    if os.path.exists(path):
        return json.load(open(path))
    for attempt in range(12):
        try:
            with _rate:  # one request at a time, ~1/s: the API answers bursts with 429 + Retry-After
                time.sleep(1.0)
                r = requests.get(API, params={**params, "format": "json"}, headers=UA, timeout=60)
                if r.status_code == 429:
                    time.sleep(int(r.headers.get("retry-after") or 30) + 2)
            if r.status_code == 200:
                data = r.json()
                with _lock:
                    json.dump(data, open(path, "w"), ensure_ascii=False)
                return data
        except requests.RequestException:
            time.sleep(5)
    raise RuntimeError(f"wikidata api failed: {params}")


def search(name, lang="en"):
    return api(dict(action="wbsearchentities", search=name, language=lang, uselang="en", type="item", limit=7))["search"]


def entities(qids, props="claims|labels|descriptions|sitelinks"):
    out = {}
    qids = sorted(set(qids))
    for i in range(0, len(qids), 50):
        out.update(api(dict(action="wbgetentities", ids="|".join(qids[i:i + 50]), props=props,
                            languages="en|de|fr", sitefilter="enwiki|dewiki|jawiki")).get("entities", {}))
    return out


def year(snak):
    try:
        v = snak["datavalue"]["value"]
        return int(v["time"][:5]) if v["precision"] >= 9 else None  # year precision or better
    except (KeyError, TypeError, ValueError):
        return None


def claim_vals(ent, prop):
    for c in ent.get("claims", {}).get(prop, []):
        if c.get("rank") == "deprecated" or c["mainsnak"].get("snaktype") != "value":
            continue
        yield c


def first_year(ent, prop):
    return next((y for y in (year(c["mainsnak"]) for c in claim_vals(ent, prop)) if y), None)


def qid_of(c):
    return c["mainsnak"]["datavalue"]["value"].get("id")


def label(ent):
    L = ent.get("labels", {})
    return next((L[l]["value"] for l in ("en", "de", "fr") if l in L), ent.get("id"))


def coords(ent):
    for c in claim_vals(ent, "P625"):
        v = c["mainsnak"]["datavalue"]["value"]
        return round(v["latitude"], 4), round(v["longitude"], 4)
    return None, None


def main():
    g = json.load(open(os.path.join(OUT, "graph_books.json")))
    people = [n for n in g["nodes"] if n["type"] == "person"]
    insts = [n for n in g["nodes"] if n["type"] == "institution"]

    # ---- people: candidates
    def cands(n):
        names = [n["label"]] + [v for v in n["variants"] if len(v.split()) > 1 and v != n["label"]][:2]
        seen, out = set(), []
        for nm in names:
            for lang in ("en", "de"):
                for c in search(nm, lang):
                    if c["id"] not in seen:
                        seen.add(c["id"]); out.append(c["id"])
            if len(out) >= 4:
                break
        return out
    with ThreadPoolExecutor(6) as ex:
        pc = dict(zip((n["id"] for n in people), ex.map(cands, people)))
    ents = entities([q for qs in pc.values() for q in qs])

    def match(n):
        for q in pc[n["id"]]:
            e = ents.get(q, {})
            if not any(qid_of(c) == "Q5" for c in claim_vals(e, "P31")):
                continue
            b, d = first_year(e, "P569"), first_year(e, "P570")
            ok = [abs(a - x) <= 1 for a, x in ((n["birth_year"], b), (n["death_year"], d)) if a and x]
            if ok:
                if all(ok):
                    return q
                continue
            desc = " ".join(v["value"] for v in e.get("descriptions", {}).values())
            if not n["birth_year"] and not n["death_year"] and SCHOLARLY.search(desc):
                return q  # undated in our data: accept only an obviously scholarly namesake
        return None
    pm = {n["id"]: match(n) for n in people}
    print(f"[wd] people matched: {sum(1 for q in pm.values() if q)}/{len(people)}")

    # ---- institutions: Gemini picks among search candidates
    def icands(n):
        out = []
        for nm in [n["label"]] + n["variants"][:1]:
            for c in search(nm):
                if c["id"] not in [o["qid"] for o in out]:
                    out.append({"qid": c["id"], "label": c.get("label"), "description": c.get("description")})
        return out[:8]
    with ThreadPoolExecutor(6) as ex:
        ic = dict(zip((n["id"] for n in insts), ex.map(icands, insts)))
    client = llm.make_client()
    items = [{"id": n["id"], "name": n["label"], "city": n.get("city"), "kind": n.get("kind"), "candidates": ic[n["id"]]}
             for n in insts if ic[n["id"]]]
    schema = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "id": {"type": "STRING"}, "qid": {"type": "STRING", "nullable": True}}, "required": ["id"]}}
    im = {}
    for i in range(0, len(items), 60):
        res = llm_json(client, "For each institution (19th-century context, history of Indology) pick the Wikidata candidate "
                       "that is the same institution. For a historical university pick the item of the university itself "
                       "(e.g. Humboldt University of Berlin for 'University of Berlin'), not a city, building or faculty. "
                       "Return qid null if no candidate fits.\n\n" + json.dumps(items[i:i + 60], ensure_ascii=False), schema)
        for r in res:
            valid = {c["qid"] for c in ic.get(r["id"], [])}
            if r.get("qid") in valid:
                im[r["id"]] = r["qid"]
    print(f"[wd] institutions matched: {len(im)}/{len(insts)}")

    # ---- statements for matched people
    ents.update(entities([q for q in pm.values() if q and q not in ents]))
    ref_qids = set()
    P = {}
    for nid, q in pm.items():
        if not q:
            continue
        e = ents[q]
        st = []
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
        P[nid] = {"qid": q, "birth": first_year(e, "P569"), "death": first_year(e, "P570"),
                  "birth_place": places["P19"], "death_place": places["P20"], "image": img,
                  "enwiki": sl.get("enwiki", {}).get("title"), "dewiki": sl.get("dewiki", {}).get("title"),
                  "description": e.get("descriptions", {}).get("en", {}).get("value"), "statements": st}
    ref_qids.update(im.values())
    refs = entities(ref_qids, props="claims|labels")
    labels = {}
    for q, e in refs.items():
        lat, lon = coords(e)
        labels[q] = {"label": label(e), "lat": lat, "lon": lon, "inception": first_year(e, "P571")}
    # coordinates of a university often sit on its city: follow P131/P159 when missing
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
    json.dump({"people": P, "institutions": I, "labels": labels}, open(os.path.join(OUT, "wikidata.json"), "w"),
              ensure_ascii=False, indent=1)
    ns = sum(len(p["statements"]) for p in P.values())
    nd = sum(1 for p in P.values() for s in p["statements"] if s["start"] or s["end"])
    print(f"[wd] {ns} statements ({nd} dated), {sum(1 for p in P.values() if p['image'])} portraits, "
          f"{sum(1 for v in I.values() if v.get('lat') is not None)} institutions with coordinates")


if __name__ == "__main__":
    main()
