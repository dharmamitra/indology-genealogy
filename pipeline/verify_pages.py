#!/usr/bin/env python3
"""Check evidence quotes against the PAGE IMAGES, not against the OCR text.

Why: the OCR text of the scanned corpus was itself produced by an LLM, and it sometimes invents whole passages on blank
or illegible pages (found: a German "Vorwort" signed "Jens-Uwe Hartmann" on a blank verso of an Italian edition). A quote
that exists in the OCR text proves nothing in that case. For every relation that survived verify.py we therefore
 1. locate the OCR unit (page, or 5-page chunk in the older scheme) that contains the quote  (ocr-qnap/parsed/*.jsonl),
 2. render those pages from the PDF,
 3. flag blank pages that nevertheless carry OCR text, and otherwise ask Gemini (vision) whether each quote is printed there.
Output: data/page_verdicts.json  {relation file: [ "printed" | "not_printed" | "blank_page" | "unclear" | "unlocated" | "no_pdf" | None ]}
Only the mj-qnap corpus has per-page OCR records and reachable PDFs; other corpora are reported as not checkable here.
"""
import glob, io, json, os, re, sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

MJ = os.path.expanduser("~/data/dharmanexus-modern-japanese")
SNAP = os.environ.get("KENGO_SNAP", "/qnap/data/kengo-ocr-snapshot")
RECHECK = "--recheck" in sys.argv  # second look at quotes reported as not printed

PROMPT = """The images are consecutive pages of a scanned book. Below are sentences that an OCR system claims to have read on \
these pages. For each sentence decide whether it is really printed on the pages shown:
- "printed": the sentence (allowing small OCR differences, line breaks, hyphenation) is there
- "not_printed": it is not on these pages (e.g. the pages are blank, or show different text)
- "unclear": the scan is too poor to tell
Look at the images; do not judge by plausibility. Echo "k".

SENTENCES:
{items}"""
SCHEMA = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
    "k": {"type": "INTEGER"}, "status": {"type": "STRING", "enum": ["printed", "not_printed", "unclear"]}}, "required": ["k", "status"]}}


def norm(s):
    return re.sub(r"[\W_]+", "", s.lower())


def check_doc(job):
    """One document: render the needed units and ask Gemini. Runs in a worker process."""
    import fitz
    from google import genai
    from google.genai import types
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from extract_relations import load_key
    pdf, units = job["pdf"], job["units"]
    out = {}
    try:
        doc = fitz.open(pdf)
    except Exception:
        return {q["id"]: "no_pdf" for u in units for q in u["quotes"]}
    client = genai.Client(api_key=load_key())
    for u in units:
        pages = [p for p in range(u["start"], min(u["end"], len(doc)))]
        imgs, ink = [], []
        for p in pages:
            pm = doc[p].get_pixmap(dpi=130 if RECHECK else 100, colorspace=fitz.csGRAY)
            data = pm.samples
            dark = sum(1 for b in data[::37] if b < 140) / max(1, len(data[::37]))
            ink.append(dark)
            imgs.append(pm.tobytes("jpeg"))
        if pages and max(ink) < 0.0015:  # nothing printed on any of these pages, yet the OCR "read" text
            for q in u["quotes"]:
                out[q["id"]] = "blank_page"
            continue
        items = [{"k": k, "sentence": q.get("part") or q["quote"][:260]} for k, q in enumerate(u["quotes"])]
        status = {}
        for attempt in range(4):
            try:
                resp = client.models.generate_content(
                    model="gemini-flash-latest",
                    contents=[types.Part.from_bytes(data=b, mime_type="image/jpeg") for b in imgs] + [PROMPT.format(items=json.dumps(items, ensure_ascii=False))],
                    config=types.GenerateContentConfig(temperature=0.0, response_mime_type="application/json", response_schema=SCHEMA,
                                                       max_output_tokens=4096, thinking_config=types.ThinkingConfig(thinking_budget=0)))
                status = {r["k"]: r["status"] for r in json.loads(resp.text)}
                break
            except Exception:
                status = {}
        for k, q in enumerate(u["quotes"]):
            out[q["id"]] = status.get(k, "unclear")
    return out


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from extract_relations import DATA
    verdicts = json.load(open(os.path.join(DATA, "verdicts.json")))
    opath = os.path.join(DATA, "page_verdicts.json")
    result = json.load(open(opath)) if os.path.exists(opath) else {}
    # relations to check, per document
    CORPUS = next((a.split("=")[1] for a in sys.argv if a.startswith("--corpus=")), "mj-qnap")
    need, paths = defaultdict(list), {}
    for f in sorted(glob.glob(os.path.join(DATA, "prefaces", CORPUS, "*.json")) + glob.glob(os.path.join(DATA, "sections", CORPUS, "*.json"))):
        d = json.load(open(f)); rel = os.path.relpath(f, DATA)
        vs = verdicts.get(rel) or []
        result.setdefault(rel, [None] * len(d["relations"]))
        paths[d["docid"]] = d["path"].split("#")[0]
        for j, r in enumerate(d["relations"]):
            v = (vs[j] if j < len(vs) else None) or {}
            if r.get("quote_ok") and v.get("verdict") in ("ok", "wrong_type") and result[rel][j] in ((None, "unclear") if not RECHECK else ("not_printed",)):
                need[d["docid"]].append({"id": f"{rel}|{j}", "quote": r["evidence"]})
    print(f"[pages] {CORPUS}: {sum(len(v) for v in need.values())} quotes in {len(need)} documents to check", flush=True)
    units, pdfs, span = defaultdict(dict), {}, 5
    if CORPUS == "mj-qnap":  # per-page OCR records of the QNAP run
        wl = {w["docid"]: w for w in json.load(open(os.path.join(MJ, "ocr-qnap", "worklist.json")))}
        idx = {wl[d]["i"]: d for d in need if d in wl}
        for sh in sorted(glob.glob(os.path.join(MJ, "ocr-qnap", "parsed", "*.jsonl"))):
            for line in open(sh, encoding="utf-8"):
                m = re.match(r'\{"key": "d(\d{5})p(\d{5})"', line)
                if m and int(m.group(1)) in idx:
                    units[idx[int(m.group(1))]][int(m.group(2))] = json.loads(line).get("text") or ""
        for docid in need:
            meta = os.path.join(MJ, "ocr-qnap", "out", docid + ".json")
            src = json.load(open(meta)).get("source") if os.path.exists(meta) else None
            pdfs[docid] = os.path.join(SNAP, src) if src else None
    else:  # older OCR runs: PDFs by file name; pages from END_OF_PAGE markers (mj-data) or by proportion (mj-new)
        import fitz
        index = {}
        for root in [os.path.join(MJ, "from-kengo"), "/qnap/data/japanese-pdfs", "/qnap/data/modern-japanese"]:
            for dp, _, fn in os.walk(root):
                for x in fn:
                    if x.lower().endswith(".pdf"):
                        index.setdefault(os.path.splitext(x)[0], os.path.join(dp, x))
        span = 2
        for docid in need:
            pdfs[docid] = index.get(docid)
            try:
                text = open(paths[docid], encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            if "END_OF_PAGE" in text:
                for k, pg in enumerate(re.split(r"END_OF_PAGE_\d+", text)):
                    units[docid][k] = pg
            elif pdfs[docid]:
                try:
                    n = len(fitz.open(pdfs[docid]))
                except Exception:
                    continue
                step = max(1, len(text) // max(1, n))
                for k in range(n):  # no page marks: a page-sized slice of text per page, checked with a generous margin
                    units[docid][k] = text[max(0, k * step - step):(k + 2) * step]
                span = 4
    jobs = []
    for docid, qs in need.items():
        pdf = pdfs.get(docid)
        us = units.get(docid) or {}
        starts = sorted(us)
        nu = {s: norm(us[s]) for s in starts}
        by_unit = defaultdict(list)
        for q in qs:
            parts = [p for p in re.split(r"\.\.\.|…", q["quote"]) if len(norm(p)) >= 12] or [q["quote"]]
            part = max(parts, key=lambda p: len(norm(p))) if RECHECK else parts[0]  # re-check: one contiguous piece of a spliced quote
            key = norm(part)[:45]
            s = next((s for s in starts if key and key in nu[s]), None)
            if RECHECK:
                q["part"] = part.strip()[:260]
            rel, j = q["id"].rsplit("|", 1)
            if s is None or not pdf or not os.path.exists(pdf):
                result[rel][int(j)] = "unlocated" if pdf and os.path.exists(pdf) else "no_pdf"
            else:
                by_unit[s].append(q)
        if by_unit:
            ends = {s: (starts[k + 1] if k + 1 < len(starts) else s + span) for k, s in enumerate(starts)}
            pad = (1 if RECHECK else 0) + (0 if CORPUS == "mj-qnap" else 1)  # neighbouring pages: sentences run across page breaks
            jobs.append({"pdf": pdf, "units": [{"start": max(0, s - pad), "end": min(ends[s], s + span) + pad, "quotes": v} for s, v in sorted(by_unit.items())]})
    print(f"[pages] {len(jobs)} documents with locatable quotes; {sum(len(j['units']) for j in jobs)} page units", flush=True)
    done = 0
    with ProcessPoolExecutor(10) as ex:
        for out in ex.map(check_doc, jobs):
            for qid, st in out.items():
                rel, j = qid.rsplit("|", 1)
                result[rel][int(j)] = st
            done += 1
            if done % 50 == 0:
                json.dump(result, open(opath, "w"), ensure_ascii=False)
                print(f"[pages] {done}/{len(jobs)} documents", flush=True)
    json.dump(result, open(opath, "w"), ensure_ascii=False)
    from collections import Counter
    print("[pages]", dict(Counter(x for v in result.values() for x in v if x)))


if __name__ == "__main__":
    main()
