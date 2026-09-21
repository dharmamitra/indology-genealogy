#!/usr/bin/env python3
"""Extract history-of-Indology relations (teacher/student, positions held, ...) from OCR text
with a pool of Gemini workers.

  python3 extract_relations.py --workers 16                 # all default sources
  python3 extract_relations.py --only Win917 --limit 5      # pilot

Each source is cut into overlapping chunks; every chunk gets one Gemini call returning JSON.
Results are cached per chunk in data/chunks/<docid>/<n>.json, so reruns only do missing chunks.
Evidence quotes are checked against the chunk text (quote_ok) to catch hallucinated relations.
Key: $GEMINI_API_KEY, else ~/code/mitra-evaluation/.secrets.env. OCR texts: $INDOLOGY_OCR_DIR.
"""
import argparse, json, os, re, sys, time, threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from google import genai
from google.genai import types

MODEL = os.getenv("INDOLOGY_GEMINI_MODEL", "gemini-flash-latest")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
# directory with the OCR text of the source books (<docid>.txt); not part of this repository
SRC_DIR = os.getenv("INDOLOGY_OCR_DIR", os.path.expanduser("~/data/dharmanexus-modern-japanese/ocr-qnap/out"))
OUT_DIR = os.path.join(DATA, "chunks")
SOURCES = [
    "Wiz9081__Winternitz_GeschichteDerIndischenLitteratur_1_1908",
    "Wiz9082__Winternitz_GeschichteDerIndischenLitteratur_2_1920",
    "Wiz9083__Winternitz_GeschichteDerIndischenLitteratur_3_1920",
    "Win917__Windisch_GeschichteSanskrit-Philologie",
]
CHUNK_CHARS = 14000
OVERLAP_CHARS = 1500
MAX_RETRIES = 5

REL_TYPES = ["student_of", "studied_at", "position_at", "succeeded", "collaborated_with",
             "influenced_by", "founded", "other"]

PROMPT = """You are helping reconstruct the academic genealogy of Indology (Sanskrit philology and \
neighbouring fields) from a scholarly book. Below is one chunk of OCR text (mostly German, footnotes inline).

Extract every statement about the careers and relationships of *modern scholars* (roughly 1600-1930: \
Indologists, Orientalists, linguists, missionaries, colonial officials who worked on Indian languages). \
IGNORE ancient/medieval Indian authors, kings, mythological figures, and pure bibliographic citations \
("vgl. Weber, Ind. Stud. 3") that say nothing about a scholar's life.

Relation types:
- student_of: subject studied under / was a pupil of object (person). Also "Schüler von", "hörte bei", "angeregt durch den Unterricht von".
- studied_at: subject studied at object (university/institution/city).
- position_at: subject held a post at object (university, library, society, college, mission, government office). Put the role (Professor, Privatdozent, Bibliothekar, Boden Professor, judge ...) in "role".
- succeeded: subject was the successor of object (person) in a chair/post. Put the institution in "place".
- collaborated_with: joint editions, co-authored works, close joint work.
- influenced_by: explicit statement that subject was decisively influenced/inspired by object, without formal teaching.
- founded: subject founded object (society, journal, chair, institution).
- other: another biographically relevant link between two scholars (relative, friend, opponent in a famous controversy); say which in "role".

Rules:
- Use ONLY what this text says. Do NOT add anything from your own knowledge, not even obvious facts.
- "evidence" must be a short VERBATIM quote from the chunk (max ~200 characters, copy the OCR exactly) that supports the relation.
- "subject"/"object": the name as fully as the chunk gives it (e.g. "Rudolf Roth", not "er"). Resolve pronouns to the person they refer to.
- Give years only if stated. "year_start"/"year_end" are integers or null.
- "explicit": true if the text states the relation directly, false if you infer it from context.
- Also list every modern scholar about whom the chunk gives biographical facts in "people", with birth/death year and places if stated.
- If the chunk contains nothing relevant, return empty lists.

CHUNK:
<<<
{chunk}
>>>"""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "people": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "name": {"type": "STRING"},
            "birth_year": {"type": "INTEGER", "nullable": True},
            "death_year": {"type": "INTEGER", "nullable": True},
            "birth_place": {"type": "STRING", "nullable": True},
            "nationality": {"type": "STRING", "nullable": True},
            "note": {"type": "STRING", "nullable": True},
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


def load_key():
    if os.getenv("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    for line in open(os.path.expanduser("~/code/mitra-evaluation/.secrets.env")):
        line = line.strip()
        if line.startswith("GEMINI_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("No GEMINI_API_KEY found")


def chunk_text(text):
    """Cut on line boundaries into ~CHUNK_CHARS pieces, each prefixed with OVERLAP_CHARS of the previous."""
    lines = text.splitlines(keepends=True)
    chunks, cur, size = [], [], 0
    for ln in lines:
        cur.append(ln); size += len(ln)
        if size >= CHUNK_CHARS:
            chunks.append("".join(cur)); cur, size = [], 0
    if cur:
        chunks.append("".join(cur))
    out = []
    for i, c in enumerate(chunks):
        out.append((chunks[i - 1][-OVERLAP_CHARS:] if i else "") + c)
    return out


def _norm(s):
    return re.sub(r"[\W_]+", "", s.lower())


def quote_ok(evidence, chunk_norm):
    """True if the evidence (or, for quotes with '...', each longer piece) occurs in the chunk."""
    parts = [p for p in re.split(r"\.\.\.|…|\[\.\.\.\]", evidence) if len(_norm(p)) >= 12] or [evidence]
    return all(_norm(p) in chunk_norm for p in parts)


def run_chunk(client, docid, idx, chunk):
    path = os.path.join(OUT_DIR, docid, f"{idx:04d}.json")
    if os.path.exists(path):
        return "cached", 0, 0
    last = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.models.generate_content(
                model=MODEL, contents=PROMPT.format(chunk=chunk),
                config=types.GenerateContentConfig(
                    temperature=0.0, response_mime_type="application/json", response_schema=SCHEMA,
                    max_output_tokens=32768),
            )
            data = json.loads(resp.text)
            cn = _norm(chunk)
            for r in data["relations"]:
                r["quote_ok"] = quote_ok(r["evidence"], cn)
            data.update(docid=docid, chunk=idx, model=MODEL)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".tmp", "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(path + ".tmp", path)
            um = resp.usage_metadata
            return "ok", um.prompt_token_count or 0, (um.candidates_token_count or 0) + (um.thoughts_token_count or 0)
        except Exception as e:  # rate limits, truncated JSON, transient 5xx
            last = e
            time.sleep(min(60, 3 * 2 ** attempt))
    return f"FAILED: {last!r}"[:300], 0, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--only", help="substring filter on docid")
    ap.add_argument("--limit", type=int, help="max chunks per source (pilot)")
    ap.add_argument("--start", type=int, default=0, help="first chunk index (pilot)")
    args = ap.parse_args()

    tasks = []
    for docid in SOURCES:
        if args.only and args.only not in docid:
            continue
        chunks = chunk_text(open(os.path.join(SRC_DIR, docid + ".txt"), encoding="utf-8").read())
        sel = list(enumerate(chunks))[args.start:]
        if args.limit:
            sel = sel[:args.limit]
        print(f"[extract] {docid}: {len(chunks)} chunks, running {len(sel)}", flush=True)
        tasks += [(docid, i, c) for i, c in sel]

    client = genai.Client(api_key=load_key())
    lock, done, tin, tout, failed = threading.Lock(), 0, 0, 0, []
    with ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(run_chunk, client, d, i, c): (d, i) for d, i, c in tasks}
        for fut in as_completed(futs):
            status, a, b = fut.result()
            with lock:
                done += 1; tin += a; tout += b
                if status.startswith("FAILED"):
                    failed.append((futs[fut], status))
                if done % 20 == 0 or done == len(tasks):
                    print(f"[extract] {done}/{len(tasks)} in={tin:,} out={tout:,} failed={len(failed)}", flush=True)
    for (d, i), s in failed:
        print(f"[extract] {d} chunk {i}: {s}", file=sys.stderr)


if __name__ == "__main__":
    main()
