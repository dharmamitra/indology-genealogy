#!/usr/bin/env python3
"""ORCID for the living: dated education and employment from the public ORCID API.

1. Reverse lookup: every scholar in the graph who may be alive (no death year; born after 1935 or birth unknown with
   evidence after 1990) is searched by name. A record is accepted only if one of its organisations matches an
   institution we already have for that person (or, with a unique hit, the record carries a keyword of the field).
2. Keyword scan: profiles tagged Sanskrit / Indology / Buddhist studies / Tibetan / Pali / Vedic / Indian philosophy
   that are not in the graph are added when their affiliations are in a department the graph knows.
Records -> data/prefaces/orcid/<orcid>.json: studied_at / position_at with the years as stated (source "ORCID <id>"),
plus the person's ORCID id, taken over into the site as a link. Everything is cached in data/orcid_cache/.
"""
import json, os, re, sys, time, unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import requests

from extract_relations import DATA, ROOT

UA = {"User-Agent": "indology-genealogy/0.2 (academic genealogy research; https://github.com/dharmamitra/indology-genealogy)", "Accept": "application/json"}
API = "https://pub.orcid.org/v3.0"
CACHE = os.path.join(DATA, "orcid_cache")
OUT = os.path.join(DATA, "prefaces", "orcid")
FIELD_KW = re.compile(r"sanskrit|indolog|buddhis|tibet|pali|vedic|indian philosoph|south asia|prakrit|jain|hindu|indo-aryan|indo-iranian|mahayana|abhidharma|tantra|yoga|ayurved", re.I)
KEYWORDS = ["Sanskrit", "Indology", "Buddhist studies", "Buddhism", "Tibetan", "Pali", "Vedic", "Indian philosophy", "Buddhology", "Tibetology", "Prakrit", "Jainism"]


def strip_acc(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def fold(s):
    return re.sub(r"[^a-z ]", " ", strip_acc(s or "").lower()).split()


def get(url, params=None):
    os.makedirs(CACHE, exist_ok=True)
    key = re.sub(r"[^\w]+", "_", url.replace(API, "") + json.dumps(params or {}, sort_keys=True))[:150]
    path = os.path.join(CACHE, key + ".json")
    if os.path.exists(path):
        return json.load(open(path))
    for a in range(6):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=60)
            if r.status_code == 200:
                d = r.json(); json.dump(d, open(path, "w")); time.sleep(0.15); return d
            if r.status_code == 404:
                json.dump({}, open(path, "w")); return {}
            time.sleep(int(r.headers.get("retry-after") or 10) if r.status_code == 429 else 5 * (a + 1))
        except (requests.RequestException, ValueError):
            time.sleep(5)
    return {}


def search(q, rows=10):
    return [r["orcid-identifier"]["path"] for r in (get(f"{API}/search/", {"q": q, "rows": rows}) or {}).get("result") or []]


def year(e, k):
    return ((e.get(k) or {}).get("year") or {}).get("value")


def record(oid):
    d = get(f"{API}/{oid}/record")
    if not d or "person" not in d:
        return None
    p = d["person"]; n = p.get("name") or {}
    out = {"orcid": oid, "given": (n.get("given-names") or {}).get("value") or "", "family": (n.get("family-name") or {}).get("value") or "",
           "other": [o.get("content") for o in (p.get("other-names") or {}).get("other-name", [])],
           "keywords": [k["content"] for k in (p.get("keywords") or {}).get("keyword", [])], "edu": [], "emp": []}
    a = d.get("activities-summary") or {}
    for kind, key in (("edu", "educations"), ("emp", "employments")):
        for g in (a.get(key) or {}).get("affiliation-group", []):
            for s in g["summaries"]:
                e = list(s.values())[0]
                out[kind].append({"org": e["organization"]["name"], "city": (e["organization"].get("address") or {}).get("city"),
                                  "country": (e["organization"].get("address") or {}).get("country"), "role": e.get("role-title"),
                                  "dept": e.get("department-name"), "start": int(year(e, "start-date")) if year(e, "start-date") else None,
                                  "end": int(year(e, "end-date")) if year(e, "end-date") else None})
    return out


def org_tokens(s):
    return {w for w in fold(s) if len(w) > 3 and w not in ("university", "universitat", "universitaet", "universite", "institute", "college", "department", "school", "faculty", "studies")}


def main():
    g = json.load(open(os.path.join(ROOT, "docs", "data", "graph.json")))
    N = g["nodes"]
    known_inst = {}  # person index -> set of institution tokens
    last_year = defaultdict(int)
    for e in g["edges"]:
        if e["type"] in ("position_at", "studied_at"):
            known_inst.setdefault(e["s"], set()).update(org_tokens(N[e["t"]]["label"]))
        for y in (e.get("year_start"), e.get("year_end"), *(e.get("att") or [])):
            if y:
                last_year[e["s"]] = max(last_year[e["s"]], y)
    all_inst_tokens = set().union(*known_inst.values()) if known_inst else set()
    alive = [i for i, n in enumerate(N) if n["type"] == "person" and not n.get("death_year")
             and ((n.get("birth_year") or 0) >= 1935 or (not n.get("birth_year") and last_year[i] >= 1990))]
    print(f"[orcid] {len(alive)} possibly living scholars to look up", flush=True)

    def lookup(i):
        n = N[i]; toks = fold(n["label"])
        if len(toks) < 2:
            return None
        fam, giv = toks[-1], toks[0]
        hits = search(f'family-name:"{fam}" AND given-names:{giv}*', rows=8)
        if not hits:
            return None
        cands = []
        for oid in hits[:8]:
            r = record(oid)
            if not r:
                continue
            rf = fold(r["family"]); rg = fold(r["given"])
            if not rf or rf[-1] != fam or not rg or rg[0][0] != giv[0]:
                continue
            rtoks = set().union(*(org_tokens(x["org"]) | org_tokens(x["dept"] or "") for x in r["edu"] + r["emp"])) if r["edu"] + r["emp"] else set()
            inst_match = bool(rtoks & known_inst.get(i, set()))
            kw = bool(FIELD_KW.search(" ".join(r["keywords"] + [x["dept"] or "" for x in r["edu"] + r["emp"]])))
            cands.append((inst_match, kw, r))
        good = [c for c in cands if c[0]] or ([c for c in cands if c[1]] if len(cands) == 1 or sum(c[1] for c in cands) == 1 else [])
        return (i, good[0][2]) if good else None
    with ThreadPoolExecutor(6) as ex:
        found = [x for x in ex.map(lookup, alive) if x]
    print(f"[orcid] matched {len(found)} scholars by name + affiliation/keyword", flush=True)

    # keyword scan for people not yet in the graph
    have = {r["orcid"] for _, r in found}
    labels = {" ".join(fold(n["label"])) for n in N if n["type"] == "person"}
    extra = []
    for kw in KEYWORDS:
        for oid in search(f'keyword:"{kw}"', rows=200):
            if oid in have:
                continue
            r = record(oid)
            if not r or not r["family"]:
                continue
            have.add(oid)
            nm = " ".join(fold(r["given"] + " " + r["family"]))
            rtoks = set().union(*(org_tokens(x["org"]) for x in r["edu"] + r["emp"])) if r["edu"] + r["emp"] else set()
            if nm not in labels and rtoks & all_inst_tokens and (r["edu"] or r["emp"]):
                extra.append((None, r))
    print(f"[orcid] keyword scan: {len(extra)} new people with a known institution", flush=True)

    os.makedirs(OUT, exist_ok=True)
    n_rel = 0
    for i, r in found + extra:
        name = N[i]["label"] if i is not None else f'{r["given"]} {r["family"]}'.strip()
        rels = []
        for kind, typ in (("edu", "studied_at"), ("emp", "position_at")):
            for x in r[kind]:
                yrs = f'{x["start"] or ""}–{x["end"] or ""}'.strip("–")
                ev = f'ORCID {r["orcid"]}: {kind == "edu" and "education" or "employment"}, {x["org"]}' + (f', {x["dept"]}' if x["dept"] else "") + (f', {x["role"]}' if x["role"] else "") + (f' ({yrs})' if yrs else "")
                rels.append({"subject": name, "type": typ, "object": x["org"], "role": x["role"] or (x["dept"] if kind == "emp" else None), "place": x["city"],
                             "year_start": x["start"], "year_end": x["end"], "explicit": True, "evidence": ev, "quote_ok": True})
        if not rels:
            continue
        n_rel += len(rels)
        json.dump({"author": None, "doc_kind": "other", "doc_year": None, "people": [{"name": name, "field": ", ".join(r["keywords"][:4]) or None}],
                   "relations": rels, "docid": f"ORCID {r['orcid']}: {name}", "corpus": "orcid", "path": f"https://orcid.org/{r['orcid']}",
                   "meta": {"title": f"ORCID record of {name}", "author": "", "year": None, "language": "en", "orcid": r["orcid"], "subject": name}},
                  open(os.path.join(OUT, r["orcid"] + ".json"), "w"), ensure_ascii=False, indent=1)
    print(f"[orcid] {n_rel} relations for {len(found) + len(extra)} people")


if __name__ == "__main__":
    main()
