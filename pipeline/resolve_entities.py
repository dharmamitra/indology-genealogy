#!/usr/bin/env python3
"""Merge the per-chunk extractions into one graph.

1. collect + dedupe relations from data/chunks/*/*.json (drops relations whose quote failed verification
   unless --keep-unverified)
2. canonicalise person names with Gemini (batched by surname so variants land in the same call)
3. canonicalise institution names with Gemini
4. write data/graph_books.json (+ graph_books.graphml, edges_books.tsv)

LLM answers are cached in data/resolve_cache/, so reruns are cheap.
"""
import argparse, glob, hashlib, json, os, re, unicodedata
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor

try:
    from google import genai
    from google.genai import types
except ImportError:  # only needed for the Gemini backend; see llm.py
    genai = types = None

from extract_relations import load_key, DATA, MODEL
import llm

OUT = DATA
CACHE = os.path.join(DATA, "resolve_cache")
PERSON_OBJ = {"student_of", "succeeded", "collaborated_with", "influenced_by", "other"}
INST_OBJ = {"studied_at", "position_at", "founded"}
SRC_SHORT = {"Wiz9081": "Winternitz GIL 1", "Wiz9082": "Winternitz GIL 2", "Wiz9083": "Winternitz GIL 3",
             "Win917_": "Windisch GSP"}

PERSON_PROMPT = """Below are name strings of scholars as extracted from German books on the history of Indology \
(Winternitz, Geschichte der indischen Litteratur; Windisch, Geschichte der Sanskrit-Philologie), each with \
context snippets. Many strings are variants of the same person ("R. Roth", "Rudolf Roth", "Rudolph von Roth").

Assign every input string to a canonical person. For each input return:
- "name": the input string, unchanged
- "canonical": the person's standard full name as used in modern reference works (e.g. "Rudolf von Roth", \
"Friedrich Max Müller", "Henry Thomas Colebrooke"). Same person => exactly the same canonical string.
- "kind": "scholar" (any modern individual: scholar, missionary, official, patron, poet), \
"ambiguous" (a bare surname etc. that could be several people and the context does not decide, e.g. "Schlegel", "Holtzmann"), \
or "not_person" (groups like "Paṇḍits in Benares", institutions, ancient Indian authors, mythological figures).
- "birth_year"/"death_year": use the years given in the context if any, otherwise your own knowledge; null if unsure.
- "field": a few words (e.g. "Vedic studies", "Pali, Buddhism", "comparative linguistics"); null if unknown.
Use the context to disambiguate namesakes (Friedrich vs. August Wilhelm Schlegel; Adolf Holtzmann sr./jr.; \
Eugène vs. Émile Burnouf). Do not merge different people.

INPUT:
{items}"""

INST_PROMPT = """Below are strings naming institutions or places where scholars studied or held posts, extracted from \
German books on the history of Indology. Normalise them. For each input return:
- "name": the input string, unchanged
- "canonical": a consistent English name. Universities ALWAYS as "University of <City>" (so "Bonn", "Universität Bonn", \
"in Bonn" as a place of study/professorship => "University of Bonn"); keep proper names for others \
("Collège de France", "Asiatic Society of Bengal", "Fort William College", "East India Company", "British Museum", \
"Bodleian Library", "Royal Asiatic Society", "Société Asiatique", "Deutsche Morgenländische Gesellschaft"). \
Same institution => exactly the same string. A bare city where the context is a university chair/study => that university. \
Paris as a place of study without further detail => "Paris (unspecified)".
- "city": modern English city name, or null
- "country": modern country, or null
- "kind": "university", "college", "library_museum", "society", "journal", "mission", "government", "school", "other"

INPUT:
{items}"""

PERSON_SCHEMA = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
    "name": {"type": "STRING"}, "canonical": {"type": "STRING"},
    "kind": {"type": "STRING", "enum": ["scholar", "ambiguous", "not_person"]},
    "birth_year": {"type": "INTEGER", "nullable": True}, "death_year": {"type": "INTEGER", "nullable": True},
    "field": {"type": "STRING", "nullable": True}}, "required": ["name", "canonical", "kind"]}}
INST_SCHEMA = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
    "name": {"type": "STRING"}, "canonical": {"type": "STRING"},
    "city": {"type": "STRING", "nullable": True}, "country": {"type": "STRING", "nullable": True},
    "kind": {"type": "STRING"}}, "required": ["name", "canonical", "kind"]}}


def strip_acc(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def surname_key(name):
    s = strip_acc(name).lower().replace("ſ", "s").replace("ß", "ss")
    s = re.sub(r"\b(der|die|the)\s+(jungere|altere|younger|elder)\b|\b(jun|sen|jr|sr)\b\.?|\(.*?\)", " ", s)
    toks = re.findall(r"[a-z]{2,}", s)
    return toks[-1] if toks else s.strip()


def llm_json(client, prompt, schema):
    """Cached structured call on the active backend (Gemini or the Claude CLI), see llm.py."""
    return llm.llm_json(client, prompt, schema)


def batches(groups, max_items):
    """Pack whole groups into batches of at most ~max_items strings."""
    cur, out = [], []
    for g in groups:
        if cur and len(cur) + len(g) > max_items:
            out.append(cur); cur = []
        cur = cur + g
    if cur:
        out.append(cur)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-unverified", action="store_true", help="keep relations whose evidence quote was not found")
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()

    # 1. collect
    rels, seen, people_info, dropped = [], set(), defaultdict(list), 0
    for p in sorted(glob.glob(os.path.join(OUT, "chunks", "*", "*.json"))):
        d = json.load(open(p))
        src = SRC_SHORT.get(d["docid"][:7], d["docid"])
        for pe in d["people"]:
            people_info[pe["name"]].append(pe)
        for r in d["relations"]:
            if not r.get("quote_ok") and not args.keep_unverified:
                dropped += 1; continue
            k = (r["subject"], r["type"], r["object"], re.sub(r"\W+", "", r["evidence"])[:60])
            if k in seen:
                continue
            seen.add(k)
            r["source"], r["chunk"] = src, d["chunk"]
            rels.append(r)
    print(f"[resolve] {len(rels)} relations ({dropped} dropped: quote not found)")

    ctx = defaultdict(list)  # name string -> context snippets
    pnames, inames = Counter(), Counter()
    for r in rels:
        pnames[r["subject"]] += 1
        ctx[r["subject"]].append(r["evidence"])
        tgt = pnames if r["type"] in PERSON_OBJ else inames
        tgt[r["object"]] += 1
        ctx[r["object"]].append(f'[{r["subject"]} {r["type"]}] ' + r["evidence"])
    print(f"[resolve] {len(pnames)} person strings, {len(inames)} institution strings")

    client = llm.make_client()

    # 2. persons, batched by surname
    def pitem(n):
        info = people_info.get(n, [])
        yrs = next(((i.get("birth_year"), i.get("death_year")) for i in info if i.get("birth_year") or i.get("death_year")), None)
        return {"name": n, "years_in_text": yrs, "context": [c[:160] for c in ctx[n][:3]]}
    groups = defaultdict(list)
    for n in pnames:
        groups[surname_key(n)].append(n)
    pb = batches([sorted(groups[k]) for k in sorted(groups)], 60)
    ib = batches([[n] for n in sorted(inames, key=lambda s: strip_acc(s).lower())], 120)
    with ThreadPoolExecutor(args.workers) as ex:
        pres = list(ex.map(lambda b: llm_json(client, PERSON_PROMPT.format(
            items=json.dumps([pitem(n) for n in b], ensure_ascii=False, indent=0)), PERSON_SCHEMA), pb))
        ires = list(ex.map(lambda b: llm_json(client, INST_PROMPT.format(
            items=json.dumps([{"name": n, "context": [c[:140] for c in ctx[n][:2]]} for n in b], ensure_ascii=False, indent=0)),
            INST_SCHEMA), ib))
    pmap = {x["name"]: x for res in pres for x in res}
    imap = {x["name"]: x for res in ires for x in res}

    # 3. build graph
    nodes, edges = {}, defaultdict(lambda: {"evidence": []})

    def pnode(n):
        m = pmap.get(n)
        if not m or m["kind"] != "scholar":
            return None
        nid = "P:" + m["canonical"]
        nd = nodes.setdefault(nid, {"id": nid, "type": "person", "label": m["canonical"], "birth_year": None,
                                    "death_year": None, "field": None, "variants": []})
        for f in ("birth_year", "death_year", "field"):
            nd[f] = nd[f] or m.get(f)
        if n not in nd["variants"]:
            nd["variants"].append(n)
        return nid

    def inode(n):
        m = imap.get(n)
        if not m:
            return None
        nid = "I:" + m["canonical"]
        nd = nodes.setdefault(nid, {"id": nid, "type": "institution", "label": m["canonical"], "city": None,
                                    "country": None, "kind": m.get("kind"), "variants": []})
        for f in ("city", "country"):
            nd[f] = nd[f] or m.get(f)
        if n not in nd["variants"]:
            nd["variants"].append(n)
        return nid

    skipped = Counter()
    for r in rels:
        s = pnode(r["subject"])
        o = pnode(r["object"]) if r["type"] in PERSON_OBJ else inode(r["object"])
        if not s or not o or s == o:
            skipped["unresolved/ambiguous/non-person endpoint"] += 1; continue
        if r["type"] == "collaborated_with" and o < s:
            s, o = o, s
        e = edges[(s, r["type"], o)]
        e.update(source=s, type=r["type"], target=o)
        e["evidence"].append({k: r.get(k) for k in ("evidence", "role", "place", "year_start", "year_end", "explicit", "source", "chunk")})
    for e in edges.values():
        ev = e["evidence"]
        e["roles"] = sorted({x["role"] for x in ev if x.get("role")})
        ys = [x["year_start"] for x in ev if x.get("year_start")]
        ye = [x["year_end"] for x in ev if x.get("year_end")]
        e["year_start"], e["year_end"] = (min(ys) if ys else None), (max(ye) if ye else None)
        e["explicit"] = any(x["explicit"] for x in ev)
        e["sources"] = sorted({x["source"] for x in ev})
    used = {e["source"] for e in edges.values()} | {e["target"] for e in edges.values()}
    graph = {"nodes": [n for n in nodes.values() if n["id"] in used], "edges": list(edges.values())}
    json.dump(graph, open(os.path.join(OUT, "graph_books.json"), "w"), ensure_ascii=False, indent=1)

    with open(os.path.join(OUT, "edges_books.tsv"), "w") as f:
        f.write("subject\trelation\tobject\troles\tyear_start\tyear_end\texplicit\tsources\tevidence\n")
        for e in sorted(graph["edges"], key=lambda e: (e["type"], e["source"], e["target"])):
            f.write("\t".join(str(x) for x in [e["source"][2:], e["type"], e["target"][2:], "; ".join(e["roles"]),
                    e["year_start"] or "", e["year_end"] or "", e["explicit"], "; ".join(e["sources"]),
                    e["evidence"][0]["evidence"].replace("\t", " ").replace("\n", " ")]) + "\n")

    try:
        import networkx as nx
        g = nx.MultiDiGraph()
        for n in graph["nodes"]:
            g.add_node(n["id"], **{k: (v if isinstance(v, (int, str)) else "; ".join(v or [])) for k, v in n.items() if v is not None and k != "id"})
        for e in graph["edges"]:
            g.add_edge(e["source"], e["target"], type=e["type"], roles="; ".join(e["roles"]),
                       year_start=e["year_start"] or 0, year_end=e["year_end"] or 0, evidence=e["evidence"][0]["evidence"])
        nx.write_graphml(g, os.path.join(OUT, "graph_books.graphml"))
    except ImportError:
        print("[resolve] networkx not installed, skipping graphml")

    tc = Counter(e["type"] for e in graph["edges"])
    print(f"[resolve] graph: {sum(n['type']=='person' for n in graph['nodes'])} people, "
          f"{sum(n['type']=='institution' for n in graph['nodes'])} institutions, {len(graph['edges'])} edges {dict(tc)}")
    print(f"[resolve] skipped: {dict(skipped)}; ambiguous names: "
          f"{sorted(n for n, m in pmap.items() if m['kind']=='ambiguous')[:40]}")


if __name__ == "__main__":
    main()
