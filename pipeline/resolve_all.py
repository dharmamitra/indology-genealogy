#!/usr/bin/env python3
"""Merge ALL extractions (book chunks, prefaces, biography sections) into one graph: data/graph_extracted.json

1. collect relations whose evidence quote was verified; label each with a short source citation
2. romanise CJK name strings (Gemini) so that 梶山雄一 / Y. Kajiyama / Kajiyama Yuichi meet in one batch
3. canonicalise people per surname batch (Gemini): canonical Latin name, native-script name, kind, fields,
   country of career, japanese flag, life dates only when known
4. canonicalise institutions to university level (Gemini)
5. keep scholars of the core fields (Indology, Buddhist studies, Tibetology) plus everybody directly tied to them

All LLM answers are cached in data/resolve_cache/.
"""
import glob, json, os, re
from collections import defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor

from google import genai

from extract_relations import load_key, DATA
from resolve_entities import llm_json, strip_acc, surname_key, batches, PERSON_OBJ, SRC_SHORT

FIELDS = ["indology", "buddhist_studies", "tibetology", "sinology", "japanology", "iranian_central_asian", "linguistics",
          "religious_studies", "philosophy", "history_archaeology", "other"]
CORE = {"indology", "buddhist_studies", "tibetology"}
CJK = re.compile(r"[぀-ヿ㐀-鿿]")

ROMAN_PROMPT = """Romanise these East Asian personal names of modern scholars (mostly Japanese; some Chinese or Korean). \
Return for each: "name" (unchanged), "family" (family name in Hepburn / pinyin / RR without diacritics, e.g. Kajiyama), \
"given" (given name without diacritics, best reading for a scholar of Buddhism or Indology), "lang" ("ja", "zh", "ko"). \
Strip titles such as 博士, 教授, 先生, 氏 before romanising. If the string is not a personal name return family "" .

{items}"""
ROMAN_SCHEMA = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
    "name": {"type": "STRING"}, "family": {"type": "STRING"}, "given": {"type": "STRING"}, "lang": {"type": "STRING"}},
    "required": ["name", "family"]}}

PERSON_PROMPT = """Below are name strings of people as extracted from prefaces, acknowledgements, obituaries and histories \
of Indology, Buddhist studies, Tibetology and neighbouring fields (1750-today), each with context snippets and, for East \
Asian names, a romanisation. Many strings are variants of one person ("R. Roth" / "Rudolph von Roth"; \
"梶山雄一" / "Y. Kajiyama" / "Kajiyama Yuichi"; "Prof. Schmithausen" / "Lambert Schmithausen").

For every input string return:
- "name": the input string, unchanged
- "canonical": the person's standard full name in Latin script, given name first also for East Asian scholars \
("Yuichi Kajiyama", "Lambert Schmithausen", "Friedrich Max Müller"); Tibetan teachers with their usual title \
("Geshe Lhundup Sopa"). Same person => exactly the same string. Expand initials only if you are sure who it is.
- "native": the name in its native script if it is not Latin (梶山雄一), else null
- "kind": "scholar" (an academic, or a traditional teacher / pandit / lama who taught modern scholars), \
"not_relevant" (family member, typist, editor at a press, funder, politician, meditation student etc.), \
"ambiguous" (bare surname or initials that could be several people and the context does not decide), \
"premodern" (lived before 1700), "not_person" (institution, group, deity)
- "fields": one to three of {fields} — what the person mainly works on. Use the context; use your knowledge if you know the scholar.
- "country": the country where the person mainly worked (modern name, e.g. "Japan", "Germany", "United States", "India"), or null
- "japanese": true if the person is a Japanese scholar or spent the career in Japanese academia
- "birth_year" / "death_year": from the context if given, otherwise from your knowledge ONLY if you are sure; else null. \
Never guess dates for living or little-known people.
Do not merge different people (namesakes, fathers and sons). Use the context to decide.

INPUT:
{items}"""
PERSON_SCHEMA = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
    "name": {"type": "STRING"}, "canonical": {"type": "STRING"}, "native": {"type": "STRING", "nullable": True},
    "kind": {"type": "STRING", "enum": ["scholar", "not_relevant", "ambiguous", "premodern", "not_person"]},
    "fields": {"type": "ARRAY", "items": {"type": "STRING", "enum": FIELDS}},
    "country": {"type": "STRING", "nullable": True}, "japanese": {"type": "BOOLEAN"},
    "birth_year": {"type": "INTEGER", "nullable": True}, "death_year": {"type": "INTEGER", "nullable": True}},
    "required": ["name", "canonical", "kind"]}}

INST_PROMPT = """Below are strings naming institutions where scholars studied or held posts (any language, 1750-today). \
Normalise each to the level of the university / academy / institute. For each input return:
- "name": the input string, unchanged
- "canonical": consistent English name. Universities as "University of <City>" or their usual English name \
("University of Tokyo" for 東京大学 / 東京帝国大学 / 東京大学文学部印度哲学科; "Kyoto University"; "Otani University"; \
"Harvard University"; "University of Hamburg" for "Universität Hamburg, Institut für Kultur und Geschichte Indiens und Tibets"; \
"École Pratique des Hautes Études"; "Collège de France"; "Sera Monastery"). Drop faculties, departments and chairs. \
Same institution => exactly the same string. A bare city in a study/teaching context => that city's university.
- "city", "country": modern English names, or null
- "kind": "university", "college", "research_institute", "academy_society", "library_museum", "monastery", \
"journal_series", "government", "school", "other"
Return canonical "" for strings that are not institutions (countries, conferences, book titles, projects, grants).

INPUT:
{items}"""
INST_SCHEMA = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
    "name": {"type": "STRING"}, "canonical": {"type": "STRING"}, "city": {"type": "STRING", "nullable": True},
    "country": {"type": "STRING", "nullable": True}, "kind": {"type": "STRING"}}, "required": ["name", "canonical"]}}


def cite(d):
    """Short source label for a preface/section record."""
    if d["docid"][:7] in SRC_SHORT:
        return SRC_SHORT[d["docid"][:7]]
    m = d.get("meta") or {}
    au = (d.get("author") or m.get("author") or "").strip()
    yr = d.get("doc_year") or m.get("year") or ""
    title = (m.get("title") or "").strip() or re.sub(r"^cleaned_", "", d["docid"])
    title = re.sub(r"\s+", " ", title)[:70]
    return " ".join(x for x in [au.split(";")[0][:40] + ("," if au else ""), str(yr) + ":" if yr else "", title] if x).strip()


def fold(s):
    return re.sub(r"[^a-z ]", "", strip_acc(s).lower().replace("-", " "))


def main():
    rels, seen, people_info, dropped = [], set(), defaultdict(list), 0
    files = sorted(glob.glob(os.path.join(DATA, "chunks", "*", "*.json")) + glob.glob(os.path.join(DATA, "prefaces", "*", "*.json"))
                   + glob.glob(os.path.join(DATA, "sections", "*", "*.json")))
    for p in files:
        d = json.load(open(p))
        src = cite(d)
        for pe in d.get("people", []):
            people_info[pe["name"]].append(pe)
        for r in d["relations"]:
            if not r.get("quote_ok"):
                dropped += 1; continue
            k = (r["subject"], r["type"], r["object"], re.sub(r"\W+", "", r["evidence"])[:60])
            if k in seen:
                continue
            seen.add(k)
            r["source"], r["doc"], r["doc_year"] = src, d["docid"][:120], d.get("doc_year") or (d.get("meta") or {}).get("year")
            rels.append(r)
    print(f"[resolve] {len(files)} records, {len(rels)} relations ({dropped} dropped: quote not found)", flush=True)

    ctx, pnames, inames = defaultdict(list), Counter(), Counter()
    for r in rels:
        pnames[r["subject"]] += 1
        ctx[r["subject"]].append(f'[{r["type"]} {r["object"][:40]}] {r["evidence"]}')
        (pnames if r["type"] in PERSON_OBJ else inames)[r["object"]] += 1
        ctx[r["object"]].append(f'[{r["subject"][:30]} {r["type"]}] {r["evidence"]}')
    print(f"[resolve] {len(pnames)} person strings, {len(inames)} institution strings", flush=True)
    client = genai.Client(api_key=load_key())

    # 2. romanise CJK names
    cjk = sorted(n for n in pnames if CJK.search(n))
    cb = [cjk[i:i + 150] for i in range(0, len(cjk), 150)]
    with ThreadPoolExecutor(16) as ex:
        rres = list(ex.map(lambda b: llm_json(client, ROMAN_PROMPT.format(items=json.dumps(b, ensure_ascii=False)), ROMAN_SCHEMA), cb))
    roman = {x["name"]: x for res in rres for x in res if x.get("family")}
    print(f"[resolve] romanised {len(roman)}/{len(cjk)} CJK names", flush=True)

    # 3. people
    def key(n):
        if n in roman:
            return fold(roman[n]["family"]).replace(" ", "") or "zz"
        return surname_key(re.sub(r"^(Prof(essor)?|Dr|Mr|Mrs|Ms|Sir|Rev|Ven|Geshe|Lama|Khenpo|Pandit|Pt|MM|Mahamahopadhyaya)\.? ", "", n)) or "zz"

    def pitem(n):
        info = people_info.get(n, [])
        yrs = next(((i.get("birth_year"), i.get("death_year")) for i in info if i.get("birth_year") or i.get("death_year")), None)
        fld = next((i.get("field") for i in info if i.get("field")), None)
        it = {"name": n, "context": [c[:170] for c in ctx[n][:3]]}
        if n in roman:
            it["romanised"] = f'{roman[n].get("given", "")} {roman[n]["family"]}'.strip()
        if yrs:
            it["years_in_text"] = yrs
        if fld:
            it["field_in_text"] = fld
        return it
    groups = defaultdict(list)
    for n in pnames:
        groups[key(n)].append(n)
    glist = []
    for k in sorted(groups):
        g = sorted(groups[k], key=lambda n: fold(roman[n].get("given", "") if n in roman else n))
        glist += [g[i:i + 70] for i in range(0, len(g), 70)]  # very common surnames are split, neighbours share initials
    pb = batches(glist, 60)
    ib = batches([[n] for n in sorted(inames, key=lambda s: strip_acc(s).lower())], 110)
    print(f"[resolve] {len(pb)} person batches, {len(ib)} institution batches", flush=True)
    pp = PERSON_PROMPT.replace("{fields}", ", ".join(FIELDS))

    def safe(fn, b):
        try:
            return fn(b)
        except Exception as e:
            print(f"[resolve] batch failed: {e!r}"[:200], flush=True)
            return []
    with ThreadPoolExecutor(64) as ex:
        pres = list(ex.map(lambda b: safe(lambda b: llm_json(client, pp.replace("{items}", json.dumps([pitem(n) for n in b], ensure_ascii=False, indent=0)), PERSON_SCHEMA), b), pb))
        ires = list(ex.map(lambda b: safe(lambda b: llm_json(client, INST_PROMPT.replace("{items}", json.dumps(
            [{"name": n, "context": [c[:140] for c in ctx[n][:2]]} for n in b], ensure_ascii=False, indent=0)), INST_SCHEMA), b), ib))
    pmap = {x["name"]: x for res in pres for x in res}
    imap = {x["name"]: x for res in ires for x in res if x.get("canonical")}

    # canonical strings that differ only by diacritics / token order are one person unless their dates conflict
    canon_by_fold = {}
    for m in pmap.values():
        if m["kind"] != "scholar":
            continue
        f = " ".join(sorted(fold(m["canonical"]).split()))
        first = canon_by_fold.setdefault(f, m)
        if first is not m and not (first.get("birth_year") and m.get("birth_year") and abs(first["birth_year"] - m["birth_year"]) > 2):
            m["canonical"] = first["canonical"]

    # 4. graph
    nodes, edges = {}, defaultdict(lambda: {"evidence": []})

    def pnode(n):
        m = pmap.get(n)
        if not m or m["kind"] != "scholar" or not m["canonical"].strip():
            return None
        nid = "P:" + m["canonical"].strip()
        nd = nodes.setdefault(nid, {"id": nid, "type": "person", "label": m["canonical"].strip(), "native": None, "birth_year": None,
                                    "death_year": None, "fields": Counter(), "country": Counter(), "japanese": 0, "variants": []})
        nd["native"] = nd["native"] or m.get("native")
        for f in ("birth_year", "death_year"):
            nd[f] = nd[f] or m.get(f)
        nd["fields"].update(m.get("fields") or [])
        if m.get("country"):
            nd["country"][m["country"]] += 1
        nd["japanese"] += 1 if m.get("japanese") else -1
        if n not in nd["variants"]:
            nd["variants"].append(n)
        return nid

    def inode(n):
        m = imap.get(n)
        if not m:
            return None
        nid = "I:" + m["canonical"].strip()
        nd = nodes.setdefault(nid, {"id": nid, "type": "institution", "label": m["canonical"].strip(), "city": None,
                                    "country": None, "kind": m.get("kind"), "variants": []})
        for f in ("city", "country"):
            nd[f] = nd[f] or m.get(f)
        if n not in nd["variants"] and len(nd["variants"]) < 12:
            nd["variants"].append(n)
        return nid

    skipped = Counter()
    for r in rels:
        s = pnode(r["subject"])
        o = pnode(r["object"]) if r["type"] in PERSON_OBJ else inode(r["object"])
        if not s or not o or s == o:
            skipped[(pmap.get(r["subject"]) or {}).get("kind", "unresolved") if not s else "object unresolved"] += 1
            continue
        if r["type"] == "collaborated_with" and o < s:
            s, o = o, s
        e = edges[(s, r["type"], o)]
        e.update(source=s, type=r["type"], target=o)
        e["evidence"].append({k: r.get(k) for k in ("evidence", "role", "place", "year_start", "year_end", "explicit", "source", "doc")})
    for e in edges.values():
        ev = e["evidence"]
        e["roles"] = sorted({x["role"] for x in ev if x.get("role")})[:4]
        ys = [x["year_start"] for x in ev if x.get("year_start")]
        ye = [x["year_end"] for x in ev if x.get("year_end")]
        e["year_start"], e["year_end"] = (min(ys) if ys else None), (max(ye) if ye else None)
        e["explicit"] = any(x["explicit"] for x in ev)
        e["sources"] = sorted({x["source"] for x in ev})
    for n in nodes.values():
        if n["type"] == "person":
            n["fields"] = [f for f, _ in n["fields"].most_common(3)]
            n["country"] = n["country"].most_common(1)[0][0] if n["country"] else None
            n["japanese"] = n["japanese"] > 0

    # 5. relevance: core-field scholars and everyone tied to them by a person-person link
    core = {n["id"] for n in nodes.values() if n["type"] == "person" and CORE & set(n["fields"])}
    keep = set(core)
    for e in edges.values():
        if e["type"] in PERSON_OBJ and (e["source"] in core or e["target"] in core):
            keep.update((e["source"], e["target"]))
    E = [e for e in edges.values() if e["source"] in keep and (e["target"] in keep or e["target"].startswith("I:"))]
    used = {e["source"] for e in E} | {e["target"] for e in E}
    graph = {"nodes": [n for n in nodes.values() if n["id"] in used], "edges": E}
    json.dump(graph, open(os.path.join(DATA, "graph_extracted.json"), "w"), ensure_ascii=False, indent=1)
    P = [n for n in graph["nodes"] if n["type"] == "person"]
    print(f"[resolve] graph: {len(P)} people ({len(core & used)} core), {len(graph['nodes']) - len(P)} institutions, {len(E)} edges "
          f"{dict(Counter(e['type'] for e in E))}")
    print(f"[resolve] fields: {dict(Counter(f for n in P for f in n['fields']))}; japanese: {sum(n['japanese'] for n in P)}")
    print(f"[resolve] skipped relations: {dict(skipped)}")


if __name__ == "__main__":
    main()
