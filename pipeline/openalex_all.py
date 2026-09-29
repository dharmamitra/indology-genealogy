#!/usr/bin/env python3
"""Branch out over co-authorship with OpenAlex (free scholarly graph, no key; polite pool via mailto).

1. Seeds: scholars of the graph who may be alive, matched to an OpenAlex author by ORCID (from data/prefaces/orcid/)
   or by name + a known institution.
2. Their works (up to 300 each, 1990 onward): every co-author becomes a `collaborated_with` link, cited by the paper
   (title, year, DOI); a co-author's institution on the paper becomes an attested `position_at` for that year.
3. Co-authors not yet in the graph are added when they share papers with two different known scholars, or share one
   paper and have a topic of the field on their OpenAlex profile.
Records -> data/prefaces/openalex/<author>.json (corpus "openalex"). Cache: data/openalex_cache/.
"""
import json, os, re, time, unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import requests

from extract_relations import DATA, ROOT

API = "https://api.openalex.org"
MAILTO = os.getenv("OPENALEX_MAILTO", "nehrdbsd@gmail.com")
UA = {"User-Agent": f"indology-genealogy/0.2 (academic genealogy research; mailto:{MAILTO})"}
CACHE = os.path.join(DATA, "openalex_cache")
OUT = os.path.join(DATA, "prefaces", "openalex")
FIELD_KW = re.compile(r"sanskrit|indolog|buddhis|tibet|\bpali\b|vedic|indian philosoph|south asia|prakrit|jain|hindu|indo-aryan|indo-iranian|asian religio|religious studies|manuscript|philolog|historical linguistic|computational linguistic|natural language", re.I)
STRICT_KW = re.compile(r"sanskrit|indolog|buddhis|tibet|\bpali\b|vedic|indian philosoph|south asia|prakrit|jain|hindu|indo-aryan", re.I)


def fold(s):
    return re.sub(r"[^a-z ]", " ", "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c)).lower()).split()


def get(path, params=None):
    os.makedirs(CACHE, exist_ok=True)
    key = re.sub(r"[^\w]+", "_", path + json.dumps(params or {}, sort_keys=True))[:150]
    cp = os.path.join(CACHE, key + ".json")
    if os.path.exists(cp):
        return json.load(open(cp))
    for a in range(6):
        try:
            r = requests.get(API + path, params={**(params or {}), "mailto": MAILTO}, headers=UA, timeout=60)
            if r.status_code == 200:
                d = r.json(); json.dump(d, open(cp, "w")); time.sleep(0.12); return d
            if r.status_code == 404:
                json.dump({}, open(cp, "w")); return {}
            time.sleep(int(r.headers.get("retry-after") or 10) if r.status_code == 429 else 5 * (a + 1))
        except (requests.RequestException, ValueError):
            time.sleep(5)
    return {}


def org_tokens(s):
    return {w for w in fold(s) if len(w) > 3 and w not in ("university", "universitat", "universitaet", "universite", "institute", "college", "department", "school", "faculty", "studies", "national")}


def main():
    g = json.load(open(os.path.join(ROOT, "docs", "data", "graph.json")))
    N = g["nodes"]
    known_inst, last_year = defaultdict(set), defaultdict(int)
    for e in g["edges"]:
        if e["type"] in ("position_at", "studied_at"):
            known_inst[e["s"]].update(org_tokens(N[e["t"]]["label"]))
        for y in (e.get("year_start"), e.get("year_end"), *(e.get("att") or [])):
            if y:
                last_year[e["s"]] = max(last_year[e["s"]], y)
    orcid_of = {}
    for f in os.listdir(os.path.join(DATA, "prefaces", "orcid")) if os.path.isdir(os.path.join(DATA, "prefaces", "orcid")) else []:
        d = json.load(open(os.path.join(DATA, "prefaces", "orcid", f)))
        orcid_of[d["meta"]["subject"]] = d["meta"]["orcid"]
    alive = [i for i, n in enumerate(N) if n["type"] == "person" and not n.get("death_year")
             and ((n.get("birth_year") or 0) >= 1935 or (not n.get("birth_year") and last_year[i] >= 1990))]
    print(f"[openalex] {len(alive)} possibly living scholars; {len(orcid_of)} with ORCID", flush=True)

    def author_for(i):
        n = N[i]
        oid = orcid_of.get(n["label"])
        if oid:
            a = get(f"/authors/https://orcid.org/{oid}")
            if a.get("id"):
                return (i, a)
        toks = fold(n["label"])
        if len(toks) < 2:
            return None
        res = (get("/authors", {"search": n["label"], "per-page": 8}) or {}).get("results") or []
        for a in res:
            at = fold(a.get("display_name"))
            if not at or at[-1] != toks[-1] or at[0][0] != toks[0][0]:
                continue
            inst = set().union(*(org_tokens(x["institution"]["display_name"]) for x in a.get("affiliations", []))) if a.get("affiliations") else set()
            topics = " ".join(t["display_name"] for t in a.get("topics", []))
            if (inst & known_inst.get(i, set())) or (len(res) == 1 and STRICT_KW.search(topics)):
                return (i, a)
        return None
    with ThreadPoolExecutor(8) as ex:
        seeds = [x for x in ex.map(author_for, alive) if x]
    print(f"[openalex] {len(seeds)} seed authors matched", flush=True)
    seed_by_aid = {a["id"]: i for i, a in seeds}

    def works(a):
        out, cursor = [], "*"
        while cursor and len(out) < 300:
            d = get("/works", {"filter": f"authorships.author.id:{a['id'].rsplit('/', 1)[1]},from_publication_date:1990-01-01", "per-page": 200, "cursor": cursor,
                               "select": "id,title,publication_year,doi,authorships,topics"})
            out += d.get("results") or []
            cursor = (d.get("meta") or {}).get("next_cursor")
        return out
    with ThreadPoolExecutor(8) as ex:
        W = dict(zip((a["id"] for _, a in seeds), ex.map(works, [a for _, a in seeds])))

    # co-authors: how many different known scholars they share papers with
    co_known, co_info, co_papers = defaultdict(set), {}, defaultdict(list)
    for aid, ws in W.items():
        for w in ws:
            auths = w.get("authorships") or []
            for x in auths:
                ca = x["author"]; cid = ca.get("id")
                if not cid or cid == aid:
                    continue
                co_known[cid].add(aid)
                co_info.setdefault(cid, {"name": ca.get("display_name"), "orcid": (ca.get("orcid") or "").rsplit("/", 1)[-1] or None})
                co_papers[cid].append((aid, w, [i["display_name"] for i in x.get("institutions", [])]))
    new_people = {}
    for cid, ks in co_known.items():
        if cid in seed_by_aid:
            continue
        if len(ks) >= 2:
            new_people[cid] = "co-author of two or more scholars of the graph"
        else:
            a = get(f"/authors/{cid.rsplit('/', 1)[1]}", {"select": "id,display_name,topics,affiliations"})
            if STRICT_KW.search(" ".join(t["display_name"] for t in a.get("topics", []))):
                new_people[cid] = "co-author with a topic of the field"
    print(f"[openalex] {len(co_known)} co-authors seen, {len(new_people)} new people admitted", flush=True)

    label_of = lambda aid: N[seed_by_aid[aid]]["label"] if aid in seed_by_aid else co_info[aid]["name"]
    os.makedirs(OUT, exist_ok=True)
    n_rel = 0
    for aid in list(seed_by_aid) + list(new_people):
        me = label_of(aid); rels = []; seen = set()
        papers = W.get(aid) or [w for _, w, _ in co_papers.get(aid, [])]
        for w in papers:
            auths = w.get("authorships") or []
            title, yr, doi = (w.get("title") or "")[:120], w.get("publication_year"), (w.get("doi") or "").replace("https://doi.org/", "")
            cite = f'co-authors of "{title}" ({yr})' + (f", doi:{doi}" if doi else "")
            for x in auths:
                cid = x["author"].get("id")
                if not cid:
                    continue
                other = label_of(cid) if (cid in seed_by_aid or cid in new_people) else None
                if cid == aid:
                    for inst in [i["display_name"] for i in x.get("institutions", [])][:2]:
                        k = ("pos", inst, yr)
                        if k not in seen and yr:
                            seen.add(k); rels.append({"subject": me, "type": "position_at", "object": inst, "role": None, "place": None, "year_start": None, "year_end": None,
                                                      "explicit": True, "evidence": f"affiliation given on \"{title}\" ({yr})", "quote_ok": True, "attested_year": yr})
                elif other and ("co", other) not in seen:
                    seen.add(("co", other))
                    rels.append({"subject": me, "type": "collaborated_with", "object": other, "role": "co-authors", "place": None, "year_start": yr, "year_end": None,
                                 "explicit": True, "evidence": cite, "quote_ok": True})
        if not rels:
            continue
        n_rel += len(rels)
        info = co_info.get(aid, {})
        json.dump({"author": None, "doc_kind": "other", "doc_year": None, "people": [{"name": me}], "relations": rels, "docid": f"OpenAlex {aid.rsplit('/', 1)[1]}: {me}",
                   "corpus": "openalex", "path": aid, "meta": {"title": f"OpenAlex works of {me}", "author": "", "year": None, "language": "en",
                   "openalex": aid, "orcid": info.get("orcid") or orcid_of.get(me), "subject": me, "why": new_people.get(aid, "seed")}},
                  open(os.path.join(OUT, aid.rsplit("/", 1)[1] + ".json"), "w"), ensure_ascii=False, indent=1)
    print(f"[openalex] {n_rel} relations written for {len(seed_by_aid) + len(new_people)} people")


if __name__ == "__main__":
    main()
