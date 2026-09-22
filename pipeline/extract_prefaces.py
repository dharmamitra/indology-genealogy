#!/usr/bin/env python3
"""Extract academic-genealogy relations from prefaces, acknowledgements, Lebensläufe, あとがき, 略歴 ...

Input : data/worklist_prefaces.jsonl (build_worklist.py) or any jsonl with the same shape (--worklist)
Output: data/prefaces/<corpus>/<sha1>.json, one per document, same record shape as data/chunks/*.json
        plus doc-level fields (author as resolved by the model, doc year, title).

  python3 pipeline/extract_prefaces.py --limit 12 --model gemini-flash-lite-latest   # pilot
  python3 pipeline/extract_prefaces.py --workers 32
  INDOLOGY_LLM=claude python3 pipeline/extract_prefaces.py --worklist data/worklist_kd-dox.jsonl --workers 6
      # same prompt and schema through the Claude CLI (subscription auth, no key); see llm.py
"""
import argparse, hashlib, json, os, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from google import genai
    from google.genai import types
except ImportError:  # only needed for the Gemini backend; see llm.py
    genai = types = None

from extract_relations import load_key, DATA, REL_TYPES, _norm, quote_ok
import llm

OUT_DIR = os.path.join(DATA, "prefaces")

PROMPT = """We are reconstructing the academic genealogy of Indology, Buddhist studies, Tibetology and neighbouring \
fields (Sanskrit, Pali, Indian philosophy, South Asian religions and history, Iranian and Central Asian studies, \
Sinology and Japanese Buddhist scholarship, comparative linguistics) from about 1750 to the present.

Below are excerpts (preface / acknowledgements / curriculum vitae / afterword / biographical note / obituary) of one \
publication, with its file name, catalogue metadata (often unreliable) and the first lines of the document.

DOCUMENT
file name: {docid}
metadata: {meta}
first lines: {head}

Tasks
1. Work out who the AUTHOR of the excerpt is (the "I"/"we"/「私」/「筆者」). If it cannot be determined, use null and \
do not extract first-person statements.
2. Extract relations between MODERN scholars (active c. 1750-today), resolving "I", "my", 私, "the author" to the author's name:
- student_of: subject studied under / was supervised by object. Put in "role" what kind: "doctoral supervisor", \
"MA supervisor", "habilitation", "teacher", "traditional teacher" (e.g. a Tibetan lama or Indian pandit who taught the \
scholar), "committee member", "postdoctoral mentor". A mere "thanks to X for comments / help / proofreading" is NOT student_of.
- studied_at: subject studied / took a degree at object (university, institute, monastery college). role = degree if stated (PhD, MA, BA, Habilitation).
- position_at: subject holds/held a post at object. role = post (Professor, Lecturer, Research Fellow, Librarian ...). \
Affiliations of thanked colleagues count if stated ("Prof. X of Hamburg University").
- succeeded: subject followed object in a chair. place = institution.
- collaborated_with: co-authors, co-editors, joint projects, reading a text together over a longer time.
- influenced_by: explicitly named as decisive intellectual influence without formal teaching.
- founded: subject founded object (institute, journal, series, society).
- other: family relation between scholars, or a named friendship/feud; say which in "role".
3. For obituaries, biographical notes and 略歴/年譜, extract the whole career of the person described (studies, teachers, posts with years).

Rules
- Use ONLY what the excerpts say; add nothing from your own knowledge (except to recognise who the author is).
- IGNORE: premodern authors, saints, lineages and teachers that are the SUBJECT of the study; family members, friends, \
typists, funding bodies, publishers, librarians thanked for services; pure citations.
- "evidence": a short VERBATIM quote from the excerpt (max ~200 characters, same script and spelling as the excerpt).
- Names: as complete as the excerpt gives them, in the script of the excerpt (keep kanji for Japanese names; do not translate).
- Years only if stated (convert Japanese era years to Western years: 昭和40年 -> 1965). For a thesis, the year of the degree goes in year_end of studied_at/student_of.
- "explicit": true if stated directly, false if inferred.
- "people": every modern scholar appearing in a relation, with birth/death year, nationality and field ONLY if the excerpt states or clearly implies them.
- If there is nothing relevant, return empty lists.

EXCERPTS
{windows}"""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "author": {"type": "STRING", "nullable": True},
        "doc_kind": {"type": "STRING", "enum": ["dissertation", "monograph", "edited_volume", "festschrift", "obituary",
                                                 "article", "journal_volume", "text_edition", "other"]},
        "doc_year": {"type": "INTEGER", "nullable": True},
        "people": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "name": {"type": "STRING"},
            "birth_year": {"type": "INTEGER", "nullable": True},
            "death_year": {"type": "INTEGER", "nullable": True},
            "nationality": {"type": "STRING", "nullable": True},
            "field": {"type": "STRING", "nullable": True},
        }, "required": ["name"]}},
        "relations": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "subject": {"type": "STRING"},
            "type": {"type": "STRING", "enum": REL_TYPES},
            "object": {"type": "STRING"},
            "role": {"type": "STRING", "nullable": True},
            "place": {"type": "STRING", "nullable": True},
            "year_start": {"type": "INTEGER", "nullable": True},
            "year_end": {"type": "INTEGER", "nullable": True},
            "explicit": {"type": "BOOLEAN"},
            "evidence": {"type": "STRING"},
        }, "required": ["subject", "type", "object", "explicit", "evidence"]}},
    },
    "required": ["people", "relations"],
}


def author_name(a):
    """The CLI backend sometimes answers {"name": ..., "note": ...} or that as a JSON string; keep the name."""
    if isinstance(a, str) and a.strip().startswith("{"):
        try:
            a = json.loads(a)
        except ValueError:
            return a
    if isinstance(a, dict):
        return a.get("name")
    return a


def out_path(rec):
    h = hashlib.sha1(rec["path"].encode()).hexdigest()[:16]
    return os.path.join(OUT_DIR, rec["corpus"], h + ".json")


def run(client, rec, model, thinking):
    path = out_path(rec)
    if os.path.exists(path):
        return "cached", 0, 0
    wins = "\n\n".join(f"--- excerpt {i + 1} ({w['where']} matter) ---\n{w['text']}" for i, w in enumerate(rec["windows"]))
    prompt = PROMPT.format(docid=rec["docid"][:150], meta=json.dumps(rec.get("meta") or {}, ensure_ascii=False),
                           head=rec["head"][:600], windows=wins)
    last = None
    for attempt in range(5):
        try:
            data, tin, tout = llm.generate_json(client, prompt, SCHEMA, model=model, thinking=thinking, max_output_tokens=16384)
            data["author"] = author_name(data.get("author"))
            cn = _norm(wins)
            for r in data["relations"]:
                r["quote_ok"] = quote_ok(r["evidence"], cn, wins)
            data.update(docid=rec["docid"], corpus=rec["corpus"], path=rec["path"], meta=rec.get("meta"),
                        model=model if client is not None else llm.model_name())
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".tmp", "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(path + ".tmp", path)
            return "ok", tin, tout
        except Exception as e:
            last = e
            time.sleep(min(60, 3 * 2 ** attempt))
    return f"FAILED: {last!r}"[:300], 0, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--worklist", default=os.path.join(DATA, "worklist_prefaces.jsonl"))
    ap.add_argument("--workers", type=int, default=llm.workers(32))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--min-score", type=int, default=6)
    ap.add_argument("--model", default=os.getenv("INDOLOGY_PREFACE_MODEL", "gemini-flash-latest"))
    ap.add_argument("--thinking", type=int, default=None, help="thinking budget in tokens (0 = off); default: model default")
    ap.add_argument("--outdir")
    args = ap.parse_args()
    global OUT_DIR
    if args.outdir:
        OUT_DIR = args.outdir
    recs = []
    for line in open(args.worklist, encoding="utf-8"):
        r = json.loads(line)
        r["windows"] = [w for w in r["windows"] if w["score"] >= args.min_score]
        if r["windows"]:
            recs.append(r)
    if args.limit:
        step = max(1, len(recs) // args.limit)
        recs = recs[::step][:args.limit]
    print(f"[prefaces] {len(recs)} documents | model={args.model} thinking={args.thinking}", flush=True)
    client = llm.make_client()
    print(f"[prefaces] backend {llm.model_name()}", flush=True)
    lock, done, tin, tout, failed = threading.Lock(), 0, 0, 0, []
    with ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(run, client, r, args.model, args.thinking): r for r in recs}
        for fut in as_completed(futs):
            status, a, b = fut.result()
            with lock:
                done += 1; tin += a; tout += b
                if status.startswith("FAILED"):
                    failed.append((futs[fut]["docid"], status))
                if done % 100 == 0 or done == len(recs):
                    print(f"[prefaces] {done}/{len(recs)} in={tin:,} out={tout:,} failed={len(failed)}", flush=True)
    for d, s in failed[:30]:
        print(f"[prefaces] {d}: {s}", file=sys.stderr)


if __name__ == "__main__":
    main()
