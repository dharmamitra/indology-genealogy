# Indology Lineages

An academic genealogy of **Indology, Buddhist studies and Tibetology** — including their **Japanese** traditions — from
the 17th century to scholars working today: **who studied under whom, who taught where, and when**.

**Live site: https://dharmamitra.github.io/indology-genealogy/**

One graph, six lenses (Indology · Buddhist studies · Tibetology · Japan: Indology · Japan: Buddhist studies · all fields),
four views. Colour always means field of study.

- **Lineages** – every scholar placed by year of birth, teacher → student lines, plus succession in chairs,
  influence, collaboration, kinship and feuds. Click a scholar to light up the whole academic ancestry and descent;
  the side panel gives a career summary, career stations with dates, and the sentence each link rests on.
- **Chairs** – one timeline per institution: who held which post, from when to when.
- **Map** – where posts were held and where people studied, year by year (press ▶).
- **Fields** – the meta view: growth of the disciplines over time and who taught whom across fields.

Shareable state: `#lens=tibetology&view=chairs&sel=P:Giuseppe Tucci`.

## Sources

| Source | How it is read | What it contributes |
|---|---|---|
| Ernst Windisch, *Geschichte der Sanskrit-Philologie und indischen Altertumskunde* (1917–20); Moriz Winternitz, *Geschichte der indischen Litteratur* (1908–20) | whole books | the 18th–19th century core |
| Histories of the fields: de Jong, *A Brief History of Buddhist Studies in Europe and America*; Jackson, *A History of Tibetan Studies*; Lopez (ed.), *Curators of the Buddha*; Almond, *The British Discovery of Buddhism*; Rocher & Rocher, *The Making of Western Indology*; Yuyama on Burnouf; Oldenberg, *Vedaforschung*; the Whitney Memorial Meeting (JAOS 19); Cabezón, Dreyfus, Kapstein; obituaries (Stein, Bareau, Hertel …) | whole texts | Buddhist studies, Tibetology, America, France, Russia |
| Prefaces, acknowledgements, *Lebensläufe*, あとがき, 略歴 and contributor notes of ~6,500 books, dissertations, Festschriften and journal issues (two private research corpora: ~20,000 OCRed documents of Indological/Buddhological literature incl. a large Japanese collection, and ~8,000 works of Buddhist-studies and Tibetological reference literature) | only the front/back matter: located by marker phrases and scored locally, then sent to Gemini | the 20th and 21st centuries: supervisors, degrees, posts, teachers — in the scholars' own words |
| Front matter of a third private library (~12,000 PDFs of Indological, Buddhological and Japanese scholarship; text layers and Tesseract OCR): title page, imprint, dedication, preface / acknowledgements / colophon located page by page, with headings in nine languages including Sanskrit and Hindi (prastāvanā, bhūmikā) — 2,354 documents beyond the two corpora above (corpus `kd-dox`) | `pipeline/locate_frontmatter.py`, then the same extractor; quotes carry the PDF page | supervisors, teachers, posts; Indian editors' prefaces and their paṇḍits |
| Biography-dense sections anywhere else (obituary notices in JRAS, JAOS, BEFEO, Indian Antiquary …; biographical sketches) | 8k-character windows with enough career vocabulary, max. 8 per document | obituaries, careers |
| Wikipedia (de / en / ja) biographies of the ~3,200 scholars matched to Wikidata | whole articles, same extraction + verification; every link cites the article | teachers and pupils of 20th-century scholars whose own prefaces are not in the corpus ("studierte bei …", "Zu seinen Schülern zählen …"), posts with stated years |
| Department websites of the ~160 institutions with the most recent posts (people / staff / 教員紹介 pages, one level of personal pages) | found with Gemini + Google Search, fetched politely, read by Gemini; each listing is an **attestation for the crawl date** (`pipeline/crawl_departments.py`) | who is where now: current faculty, fellows and doctoral students, and their supervisors where a page says so |
| ACL Anthology and arXiv papers on Sanskrit / Pali / Tibetan / Buddhist NLP (via Semantic Scholar) | acknowledgement sections (`pipeline/fetch_computational.py`) | the computational leg of the field (field tag `computational`, own lens) |
| Editorial additions (`data/manual/relations.tsv`, `merges.tsv`) | contributed facts with a citation; shown as "[editorial addition, contributor] citation" | what the sources do not state but experts know |
| Wikidata | label lookup via SPARQL, life-date agreement | identifiers, life dates, portraits, coordinates, dated *employer* / *educated at* / *student of* statements |

The OCR texts themselves are not part of this repository; short evidence quotes are.

## How the data was made

1. **Worklists (no LLM)** – `pipeline/build_worklist.py` finds prefaces/acknowledgements/afterwords by marker phrases and
   scores them; `pipeline/build_sections.py` finds biography-dense windows and lists the books that are parsed whole.
2. **Extraction (Gemini)** – `pipeline/extract_relations.py` (whole books, 14k chunks) and `pipeline/extract_prefaces.py`
   (windows; first-person statements are resolved to the author). Relation types: `student_of` (with role: doctoral
   supervisor, teacher, traditional teacher, committee member …), `studied_at`, `position_at`, `succeeded`,
   `collaborated_with`, `influenced_by`, `founded`, `other`. The model may use **only what the text says** and must quote
   it verbatim; a relation is kept only if the quote is found in the text (about 13% are dropped).
3. **Entity resolution (Gemini)** – `pipeline/resolve_all.py`: CJK names are romanised so that 梶山雄一, *Y. Kajiyama* and
   *Kajiyama Yuichi* meet; names are canonicalised in surname batches; every person gets fields, country and a
   *japanese* flag; institutions are normalised to university level. Only scholars of the core fields (Indology,
   Buddhist studies, Tibetology) and people directly tied to them are kept.
4. **Wikidata** – `pipeline/wikidata_all.py`.
5. **Merge, harmonise, date, summarise** – `pipeline/merge_all.py`: nodes sharing a Wikidata id are merged; likely
   duplicates (spelling variants, initials, long vowels, 大学/University) are found by blocking and decided by Gemini —
   every merge is logged in `data/harmonize_merges.json`; Wikidata statements are added; missing years are filled from
   the model's own knowledge (marked); short career summaries are written from the collected facts only.

### Verification and coherence (added after the first release produced wrong date ranges)

The first release showed, e.g., *Harunaga Isaacson – Professor, University of Hamburg, 2000–2016*. Every sentence behind it
was extracted correctly (teaching posts in Hamburg 2000–2002; appointed professor 2006; listed as examiner in a 2016
thesis), but (a) the extractor had put the **publication year** of present-tense mentions into `year_end` (37% of all end
years), (b) years were sometimes inferred rather than read, and (c) all evidence for one person and place was collapsed
into one min–max range. The pipeline now has a dedicated stage:

1. `pipeline/verify.py` – a second, independent Gemini pass over **every** relation, judging only the quote: supported /
   wrong type / not supported (about 10% rejected — mostly committee members, mere thanks and name co-occurrences),
   whether the statement was *current at publication* or retrospective, and which years the quote itself states.
2. **Literal-year rule** – a year is used only if it is written in the evidence quote (Western digits, abbreviated
   ranges, kanji digits or Japanese era years). Anything else is discarded.
3. **Attestation instead of fake end dates** – a present-tense mention in a publication of year Y becomes
   `att: [first, last]` (“attested”): proof of presence, never a start or end.
4. **Stints** – posts and studies at the same place are kept as separate periods unless they overlap
   (Hamburg 2000–2002 and Hamburg 2006–, still there in 2022).
5. **Coherence rules** (`data/coherence_report.json`): years outside a person's lifetime are dropped; a teacher ≥10 years
   younger than the student drops the link, a younger teacher flags it; end before start drops both years; model-recalled
   years are requested only for identifiable scholars (Wikidata id or life dates from text/Wikidata) and rejected when
   they contradict attested years; life dates known only from the model are marked.

### Checking quotes against the page images (`pipeline/verify_pages.py`)

The scanned corpora were OCRed by an LLM, and an LLM OCR occasionally **invents text on blank or unreadable pages** — we
found a complete German "Vorwort", signed *Hamburg, im Mai 1997, Jens-Uwe Hartmann*, on the blank verso of a half-title in
an Italian edition. A quote that exists in the OCR text proves nothing in that case. Every quote from the OCRed corpora is
therefore located on its page (per-page OCR records or `END_OF_PAGE` marks), the page is rendered from the PDF, blank pages
with OCR text are flagged, and Gemini (vision) confirms that the sentence is printed there; spliced quotes are re-checked
piece by piece with the neighbouring pages. Of ~17,100 checkable quotes 16,800 were confirmed, 15 sat on blank pages and
~230 could not be confirmed — those relations are dropped. ~1,050 quotes have no reachable PDF and ~2,200 (corpus without
page marks) could only partly be located; they are kept. The reference-literature corpus comes from PDF text layers, not
from an LLM, and is not affected.

### Known gaps

Coverage follows the sources. A teacher–student link is only found where a preface, obituary, history, Wikipedia
article or Wikidata statement says so: about half of the well-known scholars of the core fields still have no teacher
recorded, and more have no students. Absence of a link means "not found in these sources", never "did not exist".

### How far to trust it

Each year on a link records where it comes from (`ys_src` / `ye_src`): `text` (written in the quoted sentence),
`wikidata`, or `model` (**recalled by the language model**, shown as “c.” with a dashed tag/bar); `att` holds attested years.
Model years were tested on a hold-out (`pipeline/eval_model_dates.py`, `data/model_date_eval.json`): for 795 relations
whose start year is stated in a text, the hidden year was recalled exactly in 45% of cases, within ±2 years in
68%, within ±5 in 86%, and was off by more than ten years in 5%
(high-confidence answers: 58% exact / 90% within ±5; medium: 29% / 80%).
Many of the large misses are different stints at the same place (first appointment vs. a later chair), so this is a
lower bound — but model years remain approximate leads, not citations.
Open-ended periods are closed with a modelled end (next post, death, or a cap; never before the last attestation) and
drawn fading out. Fields, countries and summaries are assigned automatically. Name merging makes mistakes in both
directions. Evidence quotes let you check every publication-derived link.

## Re-running

```bash
pip install google-genai requests networkx
export GEMINI_API_KEY=...            # or put it in ~/code/mitra-evaluation/.secrets.env
export INDOLOGY_OCR_DIR=/path/to/ocr # <docid>.txt files; the OCR texts are not part of this repo
python3 pipeline/extract_relations.py --workers 16            # whole books, cached per chunk
python3 pipeline/build_worklist.py && python3 pipeline/extract_prefaces.py --workers 40 --thinking 0
python3 pipeline/build_sections.py && python3 pipeline/extract_prefaces.py --worklist data/worklist_sections.jsonl \
        --outdir data/sections --min-score 0 --thinking 0
python3 pipeline/wikipedia_fetch.py && python3 pipeline/extract_prefaces.py --worklist data/worklist_wikipedia.jsonl \
        --outdir data/wikipedia_rel --min-score 0 --thinking 0
python3 pipeline/verify.py                                   # independent check of every relation
python3 pipeline/verify_pages.py [--corpus=mj-data|mj-new] [--recheck]   # quotes vs. page images
python3 pipeline/resolve_all.py
python3 pipeline/wikidata_all.py
python3 pipeline/merge_all.py
python3 -m http.server -d docs 8000
```

**Another library as a corpus.** `pipeline/locate_frontmatter.py --ocr-dir DIR --worklist data/worklist_NAME.jsonl --corpus NAME`
cuts the front matter out of a directory of page-separated OCR texts (`<relpath>.pdf.txt`, pages divided by form feeds,
as `pdftotext` and Tesseract write them) into a worklist that `extract_prefaces.py --worklist ...` consumes unchanged;
`pipeline/run_chain.sh` then runs verification, resolution, Wikidata and merge. Identities of an earlier run are pinned by
`data/resolve_prior.json` (`build_resolve_prior.py`, from the answer cache): only new strings are asked, with the known
names of the surname group as anchors.

**Without a Gemini key.** `INDOLOGY_LLM=claude` sends every call through the `claude` CLI (subscription auth, no key;
`pipeline/llm.py`), with the same prompts and schemas; `INDOLOGY_LLM_WORKERS` caps the parallelism. Cached answers of
either backend are reused. Evidence quotes are accepted verbatim or, when OCR interleaves columns or garbles single
characters, by an in-order word match (Latin scripts) or character trigrams (CJK, Indic) — `extract_relations.quote_match`.

To add a source (e.g. obituaries, Festschriften, Stache-Rosen's *German Indologists*), add its docid to
`SOURCES` in `extract_relations.py` and a short name to `SRC_SHORT` in `resolve_entities.py`, then re-run.

The site is plain HTML + [D3](https://d3js.org) (vendored in `docs/vendor/`), no build step. Land outline from
[world-atlas](https://github.com/topojson/world-atlas) (Natural Earth).
