#!/usr/bin/env python3
"""The INDOLOGY mailing list archive (1990–2026, list.indology.info/pipermail/indology) as a source.

 1. Signatures: the block after "--" / the last lines of a message that name an institution are an attestation of the
    poster's affiliation on the date of the message. Per poster (name + address), affiliations are collected with the
    years they were signed, and written as attested `position_at` relations (corpus "indology-list-sig"); e-mail
    addresses stay local, only names are published.
 2. Biographical messages: obituaries, "in memoriam", defence / appointment / retirement announcements are found by
    keyword density and go to Gemini through the usual extractor (worklist data/worklist_indology_list.jsonl,
    corpus "indology-list"), each message cited by date and subject.
Data: data/indology_list/*.txt.gz (downloaded, local only).
"""
import email.utils, glob, gzip, json, os, re, sys
from collections import defaultdict, Counter

from extract_relations import DATA

SRC = os.path.join(DATA, "indology_list")
OUT_SIG = os.path.join(DATA, "prefaces", "indology-list-sig")
INST_RX = re.compile(r"Universit|University|Universidad|Université|Institut|Institute|College|Academy|Akademie|Museum|Library|Bibliothek|Department|Dept\.|Faculty|School of|Centre|Center|Seminar|Research|Professor|Lecturer|Reader\b|Fellow|Emerit|Chair|Head of|Director", re.I)
BIO_RX = re.compile(r"obituar|in memoriam|passed away|died (?:on|at|yesterday|last|peacefully)|death of|sad news|nachruf|funeral|condolence|"
                    r"his (?:teacher|student|supervisor|pupil)s?|her (?:teacher|student|supervisor|pupil)s?|studied (?:under|with)|doctoral (?:thesis|dissertation)|"
                    r"defended|Habilitation|has been appointed|appointed (?:to|as)|new professor|retire|retirement|emeritus|successor|succeeded|"
                    r"was born|born in|born on|festschrift|felicitation|birthday", re.I)
QUOTE_RX = re.compile(r"^\s*>.*$|^On .{5,120} wrote:\s*$|^-----Original Message-----.*$", re.M)


def messages():
    for f in sorted(glob.glob(os.path.join(SRC, "*.txt.gz"))):
        t = gzip.open(f, "rt", encoding="utf-8", errors="ignore").read()
        for raw in re.split(r"\n(?=From .*\d{4}\n)", t):
            m = re.match(r"From \S+ .*?\n(.*?)\n\n(.*)", raw, re.S)
            if not m:
                continue
            head, body = m.group(1), m.group(2)
            fm = re.search(r"^From: (.*)$", head, re.M)
            dt = re.search(r"^Date: (.*)$", head, re.M)
            sj = re.search(r"^Subject: (.*)$", head, re.M)
            if not fm:
                continue
            # pipermail writes "user at domain (Name)"
            mm = re.match(r"\s*(\S+) at (\S+)\s*\((.*)\)", fm.group(1)) or re.match(r"\s*(.*)<(\S+)@(\S+)>", fm.group(1))
            if mm and " at " in fm.group(1):
                addr, name = f"{mm.group(1)}@{mm.group(2)}".lower(), mm.group(3).strip()
            elif mm:
                name, addr = mm.group(1).strip().strip('"'), f"{mm.group(2)}@{mm.group(3)}".lower()
            else:
                continue
            try:
                year = email.utils.parsedate_to_datetime(dt.group(1)).year if dt else int(f.split("/")[-1][:4])
            except Exception:
                year = int(f.split("/")[-1][:4])
            body = QUOTE_RX.sub("", body)
            body = re.sub(r"\n{3,}", "\n\n", body).strip()
            yield {"name": name, "addr": addr, "year": year, "subject": (sj.group(1) if sj else "").strip(), "body": body, "file": os.path.basename(f)}


def signature(body):
    """The block after a signature separator, or the last lines if they name an institution; None otherwise."""
    m = re.search(r"\n-- ?\n(.{10,600})\Z", body, re.S)
    block = m.group(1) if m else "\n".join(body.strip().splitlines()[-7:])
    lines = [l.strip() for l in block.splitlines() if l.strip() and not re.match(r"^(>|_{5,}|-{5,}|http|www\.|tel|phone|fax|mobile|\+\d)", l, re.I)]
    lines = [l for l in lines if len(l) < 90 and not re.search(r"@|\bwrote:|list\.indology|INDOLOGY mailing|unsubscribe|mailman", l, re.I)]
    if not lines or not any(INST_RX.search(l) for l in lines):
        return None
    return "\n".join(lines[-6:])


def main():
    people = defaultdict(lambda: {"names": Counter(), "sigs": defaultdict(set), "years": set(), "n": 0})
    bio = []
    n = 0
    for m in messages():
        n += 1
        p = people[m["addr"]]
        p["names"][m["name"]] += 1; p["years"].add(m["year"]); p["n"] += 1
        sig = signature(m["body"])
        if sig:
            p["sigs"][sig].add(m["year"])
        if len(m["body"]) > 400 and len(BIO_RX.findall(m["body"])) >= 3 or re.search(r"obituar|in memoriam|nachruf", m["subject"], re.I):
            bio.append(m)
    print(f"[list] {n} messages, {len(people)} addresses, {len(bio)} biographical messages", flush=True)
    # 1. signatures -> attested affiliations (a Gemini pass reads the free-form signature blocks into institution + role)
    os.makedirs(OUT_SIG, exist_ok=True)
    from google import genai
    from google.genai import types
    from extract_relations import load_key
    client = genai.Client(api_key=load_key())
    SCHEMA = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"k": {"type": "INTEGER"}, "institution": {"type": "STRING", "nullable": True}, "role": {"type": "STRING", "nullable": True}, "person_name": {"type": "STRING", "nullable": True}},
                                          "required": ["k"]}}
    items, meta = [], []
    for addr, p in people.items():
        if not p["sigs"]:
            continue
        name = p["names"].most_common(1)[0][0]
        for sig, yrs in p["sigs"].items():
            items.append({"k": len(items), "poster": name, "signature": sig}); meta.append((addr, name, sig, sorted(yrs)))
    print(f"[list] {len(items)} distinct signature blocks to read", flush=True)
    results = {}
    for i in range(0, len(items), 40):
        ch = items[i:i + 40]
        try:
            r = client.models.generate_content(model="gemini-flash-latest", contents="Each item is the signature block of a mailing-list message. Give the institution (university / institute, English name, no department) the signer belongs to, their role/title as written (Professor, PhD student, Emeritus ...), and the person's full name as signed (null if the block names no person). Null if the block names no institution. Echo k.\n\n" + json.dumps(ch, ensure_ascii=False),
                                               config=types.GenerateContentConfig(temperature=0.0, response_mime_type="application/json", response_schema=SCHEMA, thinking_config=types.ThinkingConfig(thinking_budget=0)))
            for x in json.loads(r.text):
                results[x["k"]] = x
        except Exception as e:
            print("[list] signature batch failed:", repr(e)[:100], flush=True)
        if i % 400 == 0:
            print(f"[list] signatures {i}/{len(items)}", flush=True)
    by_person = defaultdict(list)
    for k, (addr, name, sig, yrs) in enumerate(meta):
        x = results.get(k) or {}
        if x.get("institution"):
            by_person[(addr, x.get("person_name") or name)].append((x["institution"], x.get("role"), yrs, sig))
    n_rel = 0
    for (addr, name), affs in by_person.items():
        rels = []
        for inst, role, yrs, sig in affs:
            for y in yrs:
                rels.append({"subject": name, "type": "position_at", "object": inst, "role": role, "place": None, "year_start": None, "year_end": None, "explicit": True,
                             "evidence": f"signature on INDOLOGY list messages, {y}: " + sig.replace("\n", " / ")[:160], "quote_ok": True, "attested_year": y})
        n_rel += len(rels)
        json.dump({"author": None, "doc_kind": "other", "doc_year": None, "people": [{"name": name}], "relations": rels, "docid": f"INDOLOGY list signatures: {name}",
                   "corpus": "indology-list-sig", "path": "https://list.indology.info/pipermail/indology/", "meta": {"title": f"INDOLOGY list signatures of {name}", "author": "", "year": None, "language": "en", "subject": name}},
                  open(os.path.join(OUT_SIG, re.sub(r"[^\w]+", "_", addr)[:60] + ".json"), "w"), ensure_ascii=False, indent=1)
    print(f"[list] {n_rel} attested affiliations for {len(by_person)} posters", flush=True)
    # 2. biographical messages -> worklist for the extractor
    with open(os.path.join(DATA, "worklist_indology_list.jsonl"), "w", encoding="utf-8") as out:
        for m in bio:
            text = f"Subject: {m['subject']}\nFrom: {m['name']}\nDate: {m['year']}\n\n{m['body']}"[:12000]
            out.write(json.dumps({"docid": f"INDOLOGY list, {m['year']}: {m['subject'][:80]}", "path": f"indology_list/{m['file']}#{m['subject'][:40]}", "corpus": "indology-list",
                                  "meta": {"title": f"INDOLOGY list message, {m['year']}: {m['subject'][:80]}", "author": m["name"], "year": m["year"], "language": "en"},
                                  "head": text[:400], "windows": [{"where": "full", "start": 0, "score": 99, "text": text}]}, ensure_ascii=False) + "\n")
    print(f"[list] {len(bio)} biographical messages written to the worklist")


if __name__ == "__main__":
    main()
