#!/usr/bin/env python3
"""data/graph_extracted.json + data/wikidata.json -> docs/data/graph.json (+ docs/data/evidence.json)

 1. Wikidata attributes (ids, life dates, places, portraits, coordinates)
 2. merge nodes that share a Wikidata id
 3. add Wikidata's employer / educated-at / student-of statements (fill years, add missing links)
 4. HARMONISE: find likely duplicates (spelling variants, initials, long vowels, 大学 vs University ...) by blocking,
    let Gemini decide which are the same, merge; every merge is logged in data/harmonize_merges.json
 5. date fill from the model for relations that still lack a start year (ys_src/ye_src = "model", "c." in the UI)
 6. career summaries written by Gemini from the collected facts only
Every year keeps its provenance: ys_src / ye_src in {"text", "wikidata", "model"}.
"""
import argparse, difflib, json, os, re
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor

from google import genai

from extract_relations import load_key, DATA, ROOT
from resolve_entities import llm_json, strip_acc, PERSON_OBJ
from merge_sources import MODEL_PROMPT, MODEL_SCHEMA, QID_ALIAS, DATED_TYPES

SITE_DATA = os.path.join(ROOT, "docs", "data")
SRC_RANK = {"text": 3, "wikidata": 2, "model": 1, None: 0}
CORE = {"indology", "buddhist_studies", "tibetology"}  # Wikidata-only teachers/pupils are added for these fields only
# hand corrections of canonical names the model got wrong (initials expanded into something else)
LABEL_FIX = {"Kuala Lumpur Dhammajoti": "K. L. Dhammajoti"}
# sitting on a thesis committee is not teaching: such links are kept, but not as teacher -> student
COMMITTEE = re.compile(r"committee|examin|\breader\b|referee|gutachter|jury|rapporteur|opponent|審査|副査", re.I)

DUP_PROMPT = """Each group below lists records of {what} from a database built automatically from many publications. \
Records in a group have similar names and MAY be duplicates (spelling variants, initials vs full names, with/without \
diacritics, romanisation variants such as Yuichi/Yūichi/Yuuichi, married names, "University of X" vs "X University"). \
For each group decide which records are the same {what}. Be careful: fathers/sons, siblings and unrelated namesakes \
with different dates, fields or countries are NOT the same. Use your knowledge of the scholars where you have it.
Return, for each group that contains duplicates, one entry per set of identical records: \
{{"ids": [all ids of the same {what}], "canonical": best full name for the merged record}}. \
Omit records that have no duplicate. Return [] if there are none.

{items}"""
DUP_SCHEMA = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
    "ids": {"type": "ARRAY", "items": {"type": "STRING"}}, "canonical": {"type": "STRING"}}, "required": ["ids", "canonical"]}}

SUM_PROMPT = """Write a short career summary for each scholar using ONLY the facts listed (they come from a database; \
years marked ~ are approximate; \"attested 2004-2016\" means publications of those years mention the affiliation, not that it began or ended then). One or two sentences, at most 45 words, plain English, no praise: field, where and \
with whom they studied, main posts with years where given. Do not add facts. Echo "id" unchanged.

{items}"""
SUM_SCHEMA = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"id": {"type": "STRING"}, "summary": {"type": "STRING"}},
                                          "required": ["id", "summary"]}}


def fold(s):
    s = strip_acc(s).lower()
    return re.sub(r"[^a-z ]", " ", s)


def jp_fold(s):
    return re.sub(r"oh(?![aeiou])|ou|oo", "o", re.sub(r"uu", "u", fold(s))).replace(" ", "")


class UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        while self.p.get(x, x) != x:
            self.p[x] = self.p.get(self.p[x], self.p[x]); x = self.p[x]
        return x

    def union(self, keep, drop):
        a, b = self.find(keep), self.find(drop)
        if a != b:
            self.p[b] = a


STINT_TYPES = {"position_at", "studied_at"}


def interval(e):
    a = e["year_start"] or e.get("att_min") or e["year_end"]
    b = e["year_end"] or e.get("att_max") or e["year_start"]
    return (a, max(a, b)) if a else None


def combine(o, e):
    o["evidence"] += e["evidence"]
    o["roles"] = sorted(set(o["roles"]) | set(e["roles"]))[:4]
    o["sources"] = sorted(set(o["sources"]) | set(e["sources"]))
    o["explicit"] = o["explicit"] or e["explicit"]
    for y, sf, f in (("year_start", "ys_src", min), ("year_end", "ye_src", max)):
        if e[y] and (not o[y] or SRC_RANK[e[sf]] > SRC_RANK[o[sf]]):
            o[y], o[sf] = e[y], e[sf]
        elif e[y] and SRC_RANK[e[sf]] == SRC_RANK[o[sf]]:
            o[y] = f(o[y], e[y])
    for k, f in (("att_min", min), ("att_max", max)):
        if e.get(k):
            o[k] = f(o[k], e[k]) if o.get(k) else e[k]


def merge_edges(edges, uf, nodes):
    """Re-key edges after node merges. Posts and studies keep separate STINTS: two edges for the same person and place
    are combined only if their periods overlap or touch; undated ones join the best-documented stint."""
    groups = defaultdict(list)
    for e in edges:
        s, t = uf.find(e["source"]), uf.find(e["target"])
        if s == t or s not in nodes or t not in nodes:
            continue
        if e["type"] == "collaborated_with" and t < s:
            s, t = t, s
        e["source"], e["target"] = s, t
        groups[(s, e["type"], t)].append(e)
    out = []
    for (s, typ, t), es in groups.items():
        if typ not in STINT_TYPES:
            o = es[0]
            for e in es[1:]:
                combine(o, e)
            out.append(o); continue
        dated = sorted((e for e in es if interval(e)), key=lambda e: (interval(e)[0], -SRC_RANK[e["ys_src"]]))
        cur = []
        for e in dated:
            c, d = interval(e)
            o = next((o for o in cur if c <= interval(o)[1] + 1 and d >= interval(o)[0] - 1), None)
            if o:
                combine(o, e)
            else:
                cur.append(e)
        for e in (e for e in es if not interval(e)):
            if cur:
                combine(max(cur, key=lambda o: len(o["evidence"])), e)
            else:
                cur.append(e)
        out += cur
    return out


def absorb(nodes, keep, drop):
    k, d = nodes[keep], nodes.pop(drop)
    k["variants"] = list(dict.fromkeys(k.get("variants", []) + [d["label"]] + d.get("variants", [])))[:14]
    for f, v in d.items():
        if f not in ("id", "label", "variants") and not k.get(f) and v:
            k[f] = v
    if k["type"] == "person":
        k["fields"] = list(dict.fromkeys((k.get("fields") or []) + (d.get("fields") or [])))[:3]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-model", action="store_true", help="skip model date fill and summaries")
    args = ap.parse_args()
    g = json.load(open(os.path.join(DATA, "graph_extracted.json")))
    wd = json.load(open(os.path.join(DATA, "wikidata.json")))
    nodes = {n["id"]: n for n in g["nodes"]}
    uf, log = UF(), []
    for n in nodes.values():
        n["label"] = LABEL_FIX.get(n["label"], n["label"])
    client = genai.Client(api_key=load_key())

    # ---- 1+2. wikidata attributes, QID merges
    by_qid = {}
    for kind in ("people", "institutions"):
        for nid, w in wd[kind].items():
            if nid not in nodes:
                continue
            n = nodes[nid]
            if w["qid"] in by_qid:
                keep = by_qid[w["qid"]]
                log.append({"keep": keep, "drop": nid, "why": "same Wikidata id " + w["qid"]})
                uf.union(keep, nid); absorb(nodes, keep, nid)
                continue
            by_qid[w["qid"]] = nid
            if kind == "people":
                n.update(qid=w["qid"], image=w["image"], enwiki=w["enwiki"], dewiki=w["dewiki"], jawiki=w.get("jawiki"),
                         birth_place=w["birth_place"], death_place=w["death_place"], description=w["description"])
                for k, wk in (("birth_year", "birth"), ("death_year", "death")):
                    if w[wk]:
                        n[k], n[wk + "_src"] = w[wk], "wikidata"
            else:
                n.update(qid=w["qid"], lat=w.get("lat"), lon=w.get("lon"), inception=w.get("inception"))
    for q, nid in QID_ALIAS.items():
        if nid in nodes:
            by_qid.setdefault(q, nid)

    # ---- 3. edges: books/prefaces first, then wikidata statements
    E = g["edges"]
    for e in E:
        e["ys_src"] = "text" if e["year_start"] else None
        e["ye_src"] = "text" if e["year_end"] else None
    stats = Counter()
    for nid, w in wd["people"].items():
        src0 = uf.find(nid)
        if src0 not in nodes:
            continue
        for s in w["statements"]:
            kind = s["kind"]
            if kind in ("student_of", "teacher_of"):
                other = by_qid.get(s["qid"])
                if not other:  # teacher or pupil known to Wikidata but not (yet) to our texts: add the person
                    info = wd["labels"].get(s["qid"])
                    if not info or not info["label"] or re.fullmatch(r"Q\d+", info["label"]) or not (CORE & set(nodes[src0].get("fields") or [])):
                        continue
                    other = "P:" + info["label"]
                    if other not in nodes:
                        nodes[other] = {"id": other, "type": "person", "label": info["label"], "fields": [], "variants": [], "qid": s["qid"], "wikidata_only": True}
                    by_qid[s["qid"]] = other
                if not other.startswith("P:") or other == src0:
                    continue
                src, tgt, typ = (src0, other, "student_of") if kind == "student_of" else (other, src0, "student_of")
            else:
                tgt = by_qid.get(s["qid"])
                if not tgt:
                    info = wd["labels"].get(s["qid"])
                    if not info or not info["label"] or re.fullmatch(r"Q\d+", info["label"]):
                        continue
                    tgt = "I:" + info["label"]
                    if tgt not in nodes:
                        nodes[tgt] = {"id": tgt, "type": "institution", "label": info["label"], "city": None, "country": None,
                                      "kind": "other", "variants": [], "qid": s["qid"], "lat": info["lat"], "lon": info["lon"],
                                      "inception": info.get("inception"), "wikidata_only": True}
                    by_qid[s["qid"]] = tgt
                src, typ = src0, kind
            E.append({"source": src, "type": typ, "target": tgt, "evidence": [], "roles": [], "explicit": True, "sources": ["Wikidata"],
                      "year_start": s["start"], "year_end": s["end"], "ys_src": "wikidata" if s["start"] else None,
                      "ye_src": "wikidata" if s["end"] else None, "att_min": None, "att_max": None})
            stats[typ] += 1
    E = merge_edges(E, uf, nodes)
    print(f"[merge] wikidata statements added: {dict(stats)}; {len(log)} QID merges; {len(E)} edges", flush=True)

    # ---- 4a. editorial merges (data/manual/merges.tsv: keep<TAB>drop) — e.g. Wylie vs phonetic spellings of Tibetan names
    mpath = os.path.join(DATA, "manual", "merges.tsv")
    if os.path.exists(mpath):
        for line in open(mpath, encoding="utf-8"):
            if not line.strip() or line.startswith("#"):
                continue
            keep, drop = [("P:" if not x.startswith(("P:", "I:")) else "") + x.strip() for x in line.rstrip("\n").split("\t")[:2]]
            keep, drop = uf.find(keep), uf.find(drop)
            if keep in nodes and drop in nodes and keep != drop:
                log.append({"keep": keep, "drop": drop, "why": "editorial merge"}); uf.union(keep, drop); absorb(nodes, keep, drop)
        E = merge_edges(E, uf, nodes)
    # ---- 4b. harmonise
    nbrs = defaultdict(Counter)
    for e in E:
        nbrs[e["source"]][nodes[e["target"]]["label"]] += 1; nbrs[e["target"]][nodes[e["source"]]["label"]] += 1

    def card(n):
        c = {"id": n["id"], "name": n["label"], "links": [k for k, _ in nbrs[n["id"]].most_common(5)]}
        for f in ("native", "birth_year", "death_year", "fields", "country", "city", "kind"):
            if n.get(f):
                c[f] = n[f]
        if n.get("variants"):
            c["also_written"] = n["variants"][:4]
        return c
    people = [n for n in nodes.values() if n["type"] == "person"]
    blocks = defaultdict(list)
    for n in people:
        toks = fold(n["label"]).split()
        if toks:
            blocks[jp_fold(toks[-1])].append(n)
            if len(toks) > 1 and n.get("japanese"):
                blocks[jp_fold(toks[0])].append(n)  # family name first in some records
    pair = UF(); members = set()
    for b in blocks.values():
        if len(b) < 2 or len(b) > 400:
            continue
        for i, a in enumerate(b):
            ta, fa = fold(a["label"]).split(), jp_fold(a["label"])
            for c in b[i + 1:]:
                if a.get("birth_year") and c.get("birth_year") and abs(a["birth_year"] - c["birth_year"]) > 2:
                    continue
                tc, fc = fold(c["label"]).split(), jp_fold(c["label"])
                ga, gc = "".join(ta[:-1]), "".join(tc[:-1])
                same = (fa == fc or "".join(sorted(ta)) == "".join(sorted(tc)) or (a.get("native") and a.get("native") == c.get("native"))
                        or (ga and gc and (ga.startswith(gc[:1]) and (len(ta[0]) <= 2 or len(tc[0]) <= 2) and ga[0] == gc[0]))
                        or difflib.SequenceMatcher(None, fa, fc).ratio() >= 0.87)
                if same:
                    pair.union(a["id"], c["id"]); members.update((a["id"], c["id"]))
    groups = defaultdict(list)
    for m in members:
        groups[pair.find(m)].append(m)
    pgroups = [sorted(v) for v in groups.values() if 2 <= len(v) <= 14]
    # institutions: same city (or no city) and similar names
    iblocks = defaultdict(list)
    for n in nodes.values():
        if n["type"] == "institution":
            iblocks[(n.get("city") or "?", n.get("country") or "?")].append(n)
    igroups = []
    for (city, _), b in iblocks.items():
        if len(b) < 2:
            continue
        if city != "?" and len(b) <= 30:
            igroups.append(sorted(n["id"] for n in b)); continue
        ip = UF(); mem = set()
        for i, a in enumerate(b[:1500]):
            for c in b[i + 1:1500]:
                if difflib.SequenceMatcher(None, fold(a["label"]), fold(c["label"])).ratio() >= 0.86:
                    ip.union(a["id"], c["id"]); mem.update((a["id"], c["id"]))
        gg = defaultdict(list)
        for m in mem:
            gg[ip.find(m)].append(m)
        igroups += [sorted(v) for v in gg.values() if len(v) <= 30]
    print(f"[merge] harmonise: {len(pgroups)} person groups, {len(igroups)} institution groups to check", flush=True)

    def ask(what, grs):
        calls = [grs[i:i + 20] for i in range(0, len(grs), 20)]

        def one(ch):
            try:
                return llm_json(client, DUP_PROMPT.format(what=what, items=json.dumps(
                    [{"group": gi, "records": [card(nodes[i]) for i in gr]} for gi, gr in enumerate(ch)], ensure_ascii=False, indent=0)), DUP_SCHEMA)
            except Exception as e:
                print("[merge] harmonise batch failed:", repr(e)[:150]); return []
        with ThreadPoolExecutor(16) as ex:
            return [r for rs in ex.map(one, calls) for r in rs]
    for what, grs in (("person", pgroups), ("institution", igroups)):
        valid = {i for gr in grs for i in gr}
        for r in ask(what, grs):
            ids = [i for i in dict.fromkeys(r["ids"]) if i in valid and i in nodes]
            if len(ids) < 2 or len({i[0] for i in ids}) > 1:
                continue
            keep = max(ids, key=lambda i: (bool(nodes[i].get("qid")), sum(nbrs[i].values())))
            for d in ids:
                if d != keep and d in nodes:
                    log.append({"keep": keep, "drop": d, "why": "harmonise (Gemini)", "canonical": r["canonical"]})
                    uf.union(keep, d); absorb(nodes, keep, d)
            if r.get("canonical") and not nodes[keep].get("qid") and what == "person":
                if r["canonical"] != nodes[keep]["label"]:
                    nodes[keep]["variants"] = list(dict.fromkeys([nodes[keep]["label"]] + nodes[keep]["variants"]))[:14]
                    nodes[keep]["label"] = r["canonical"]
    E = merge_edges(E, uf, nodes)
    json.dump(log, open(os.path.join(DATA, "harmonize_merges.json"), "w"), ensure_ascii=False, indent=1)
    print(f"[merge] {len(log)} merges in total; {len(E)} edges", flush=True)

    # ---- 5a. coherence rules (logged in data/coherence_report.json)
    rep, examples = Counter(), defaultdict(list)

    def note(rule, e, extra=""):
        rep[rule] += 1
        if len(examples[rule]) < 40:
            examples[rule].append(f'{nodes[e["source"]]["label"]} -[{e["type"]}]-> {nodes[e["target"]]["label"]} {e.get("year_start")}-{e.get("year_end")} {extra}'.strip())
    solid = lambda n, k: n.get(k + "_year") if n.get(k + "_src") != "model" else None  # life dates we can lean on
    keep = []
    for e in E:
        p = nodes[e["source"]]
        b, d = solid(p, "birth"), solid(p, "death")
        lo = (b + (12 if e["type"] in ("studied_at", "student_of") else 17)) if b else 1600
        hi = min(d if d and e["type"] != "founded" else 2026, 2026)
        for y, sf in (("year_start", "ys_src"), ("year_end", "ye_src"), ("att_min", None), ("att_max", None)):
            if e.get(y) and not (lo <= e[y] <= hi):
                note("year outside the person's lifetime: dropped", e, f"({y}={e[y]}, life {b}-{d})")
                e[y] = None
                if sf:
                    e[sf] = None
        e["att_min"], e["att_max"] = (e.get("att_min") or e.get("att_max")), (e.get("att_max") or e.get("att_min"))
        if e["year_start"] and e["year_end"] and e["year_end"] < e["year_start"]:
            note("end before start: both years dropped", e); e["year_start"] = e["year_end"] = e["ys_src"] = e["ye_src"] = None
        if e["type"] == "student_of" and e["roles"] and all(COMMITTEE.search(r) for r in e["roles"]):
            e["type"], e["roles"] = "other", ["thesis committee / examiner"]
            note("committee member or examiner only: no longer counted as teacher", e)
        if e["type"] == "student_of":
            t = nodes[e["target"]]
            sb, tb = p.get("birth_year"), t.get("birth_year")
            if sb and tb and tb - sb >= 10 and "model" not in (p.get("birth_src"), t.get("birth_src")) and not any("traditional" in r.lower() for r in e["roles"]):
                note("teacher at least 10 years younger than the student: link dropped", e, f"(student b. {sb}, teacher b. {tb})"); continue
            if sb and tb and tb > sb:
                e["suspect"] = True; note("teacher younger than the student: flagged", e, f"(student b. {sb}, teacher b. {tb})")
        keep.append(e)
    E = merge_edges(keep, uf, nodes)  # links that lost their years fold back into their dated sibling stints

    # ---- 5b. model date fill — only for identifiable scholars, and only if consistent with what the texts attest
    if not args.no_model:
        todo = defaultdict(list)
        for i, e in enumerate(E):
            p = nodes[e["source"]]
            if e["type"] in DATED_TYPES and not e["year_start"] and (p.get("qid") or p.get("birth_src") in ("text", "wikidata")):
                todo[e["source"]].append(i)
        persons = sorted(todo)
        calls = [persons[b:b + 12] for b in range(0, len(persons), 12)]

        def fill(ch):
            items = [{"scholar": nodes[pid]["label"], "native_name": nodes[pid].get("native"),
                      "life": f'{nodes[pid].get("birth_year") or "?"}-{nodes[pid].get("death_year") or "?"}',
                      "relations": [{"k": i, "type": E[i]["type"], "object": nodes[E[i]["target"]]["label"], "role": "; ".join(E[i]["roles"]) or None,
                                     "known_end": E[i]["year_end"],
                                     "attested_in_publications_of": [E[i]["att_min"], E[i]["att_max"]] if E[i].get("att_min") else None}
                                    for i in todo[pid][:25]]} for pid in ch]
            try:
                return llm_json(client, MODEL_PROMPT.format(items=json.dumps(items, ensure_ascii=False, indent=0)), MODEL_SCHEMA)
            except Exception as e:
                print("[merge] date batch failed:", repr(e)[:120]); return []
        filled = 0
        with ThreadPoolExecutor(24) as ex:
            for res in ex.map(fill, calls):
                for r in res:
                    if r.get("confidence") == "low" or not (0 <= r["k"] < len(E)):
                        continue
                    e = E[r["k"]]; p = nodes[e["source"]]
                    lo, hi = (p.get("birth_year") or 1500) + 12, (p.get("death_year") or 2026)
                    ys, ye = r.get("year_start"), r.get("year_end") or e["year_end"]
                    if not ys or not (lo <= ys <= hi) or (ye and ye < ys):
                        continue
                    # a recalled START later than the first attestation is a contradiction; a recalled END before the last
                    # attestation is not (emeriti keep being listed under their university)
                    if e.get("att_min") and ys > e["att_min"] + 1:
                        note("model years contradict attested years: not used", e, f"(model {ys}-{r.get('year_end')}, attested {e['att_min']}-{e['att_max']})"); continue
                    e["year_start"], e["ys_src"] = ys, "model"; filled += 1
                    if r.get("year_end") and not e["year_end"] and lo <= r["year_end"] <= hi:
                        e["year_end"], e["ye_src"] = r["year_end"], "model"; filled += 1
        print(f"[merge] model filled {filled} years on {sum(len(v) for v in todo.values())} undated relations of identifiable scholars", flush=True)
    # a stint dated only by the model that overlaps a stint dated by a text or Wikidata is the same stint: fold it in,
    # without its recalled years (they must not glue separate documented stints together)
    groups = defaultdict(list)
    for e in E:
        groups[(e["source"], e["type"], e["target"])].append(e)
    folded = []
    for es in groups.values():
        hard = [e for e in es if "text" in (e["ys_src"], e["ye_src"]) or "wikidata" in (e["ys_src"], e["ye_src"])]
        for e in es:
            if e in hard or e["type"] not in STINT_TYPES or not hard or e["ys_src"] != "model":
                folded.append(e); continue
            a0, b0 = e["year_start"], e["year_end"] or e["year_start"]
            o = next((o for o in hard if interval(o) and a0 <= interval(o)[1] + 1 and b0 >= interval(o)[0] - 1), None)
            if not o:
                folded.append(e); continue
            e["year_start"] = e["year_end"] = e["ys_src"] = e["ye_src"] = None
            combine(o, e); note("model-dated stint folded into a documented stint", o)
    E = folded
    json.dump({"counts": dict(rep), "examples": examples}, open(os.path.join(DATA, "coherence_report.json"), "w"), ensure_ascii=False, indent=1)
    print(f"[merge] coherence: {dict(rep)}", flush=True)
    for e in E:
        if e["year_start"] and e["year_end"] and e["year_end"] < e["year_start"]:
            e["year_end"], e["ye_src"] = None, None

    # ---- 6. career summaries
    used = {e["source"] for e in E} | {e["target"] for e in E}
    if not args.no_model:
        facts = defaultdict(list)
        att = lambda e: f" (attested {e['att_min']}" + (f"-{e['att_max']}" if e["att_max"] != e["att_min"] else "") + ")" if e.get("att_min") and not (e["year_start"] or e["year_end"]) else ""
        yr0 = lambda e: "".join([" ", "~" if "model" in (e["ys_src"], e["ye_src"]) else "", str(e["year_start"] or ""), "-" if e["year_end"] else "", str(e["year_end"] or "")]).rstrip() if (e["year_start"] or e["year_end"]) else ""
        yr = lambda e: yr0(e) + att(e)
        for e in E:
            t = nodes[e["target"]]["label"]
            if e["type"] == "student_of":
                facts[e["source"]].append(f"studied under {t}{yr(e)}" + (f" ({e['roles'][0]})" if e["roles"] else ""))
                facts[e["target"]].append(f"taught {nodes[e['source']]['label']}")
            elif e["type"] == "studied_at":
                facts[e["source"]].append(f"studied at {t}{yr(e)}" + (f" ({e['roles'][0]})" if e["roles"] else ""))
            elif e["type"] == "position_at":
                facts[e["source"]].append(f"post at {t}{yr(e)}" + (f": {e['roles'][0]}" if e["roles"] else ""))
            elif e["type"] == "succeeded":
                facts[e["source"]].append(f"succeeded {t}{yr(e)}")
            elif e["type"] == "founded":
                facts[e["source"]].append(f"founded {t}{yr(e)}")
        who = sorted(p for p, f in facts.items() if len([x for x in f if not x.startswith("taught ")]) >= 2 and p in nodes)
        calls = [who[i:i + 20] for i in range(0, len(who), 20)]

        def summ(ch):
            items = [{"id": p, "name": nodes[p]["label"], "life": f'{nodes[p].get("birth_year") or "?"}-{nodes[p].get("death_year") or ""}',
                      "fields": nodes[p].get("fields"), "facts": facts[p][:24]} for p in ch]
            try:
                return llm_json(client, SUM_PROMPT.format(items=json.dumps(items, ensure_ascii=False, indent=0)), SUM_SCHEMA)
            except Exception as e:
                print("[merge] summary batch failed:", repr(e)[:120]); return []
        ns = 0
        with ThreadPoolExecutor(24) as ex:
            for res in ex.map(summ, calls):
                for r in res:
                    if r["id"] in nodes and r.get("summary"):
                        nodes[r["id"]]["summary"] = r["summary"].strip(); ns += 1
        print(f"[merge] {ns} career summaries", flush=True)

    # ---- output: structure and evidence separately (the evidence file is loaded lazily by the site)
    N = [n for n in nodes.values() if n["id"] in used]
    idx = {n["id"]: i for i, n in enumerate(N)}
    evidence = {}
    for i, e in enumerate(E):
        ev = [{k: v for k, v in x.items() if k in ("evidence", "source", "place", "explicit") and v not in (None, True)} for x in e["evidence"][:2]]
        if ev:
            evidence[i] = ev
        e["n_ev"] = len(e["evidence"]); del e["evidence"]; e.pop("stint", None)
        if e.get("att_min"):
            e["att"] = [e["att_min"], e["att_max"]]
        e.pop("att_min", None); e.pop("att_max", None)
        e["s"], e["t"] = idx[e.pop("source")], idx[e.pop("target")]
        e["books"] = any(s != "Wikidata" for s in e["sources"]); e["wd"] = "Wikidata" in e["sources"]
        e["src"] = [s for s in e.pop("sources") if s != "Wikidata"][:3]
        for k in [k for k, v in e.items() if v in (None, [], False) and k not in ("s", "t")]:
            del e[k]
    for n in N:
        if "model" in (n.get("birth_src"), n.get("death_src")):
            n["dates_model"] = True  # life dates recalled by the model, shown with a caveat
        for k in ("birth_src", "death_src"):
            n.pop(k, None)
        for k in [k for k, v in n.items() if v in (None, [], "", False)]:
            del n[k]
    os.makedirs(SITE_DATA, exist_ok=True)
    import time  # build stamp: the site asks for it uncached and appends it to the data URLs, so browsers never show stale data
    json.dump({"v": int(time.time()), "built": time.strftime("%Y-%m-%d %H:%M")}, open(os.path.join(SITE_DATA, "version.json"), "w"))
    json.dump({"nodes": N, "edges": E}, open(os.path.join(SITE_DATA, "graph.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    json.dump(evidence, open(os.path.join(SITE_DATA, "evidence.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    P = [n for n in N if n["type"] == "person"]
    c = defaultdict(Counter)
    for e in E:
        c[e["type"]]["n"] += 1; c[e["type"]][e.get("ys_src") or "undated"] += 1
    for t, v in c.items():
        print(f"[merge] {t:18s} {dict(v)}")
    print(f"[merge] {len(P)} people ({sum(1 for n in P if n.get('japanese'))} japanese), {len(N) - len(P)} institutions, {len(E)} edges; "
          f"fields {dict(Counter(f for n in P for f in n.get('fields', [])))}")
    for f in ("graph.json", "evidence.json"):
        print(f"[merge] {f}: {os.path.getsize(os.path.join(SITE_DATA, f)) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
