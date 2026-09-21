# Indology Lineages

An academic genealogy of Indology (Sanskrit philology and its neighbours), c. 1650–1920: **who studied under
whom, who taught where, and when**.

**Live site: https://dharmamitra.github.io/indology-genealogy/**

Three views over one graph:

- **Lineages** – every scholar placed by year of birth, teacher → student lines, plus succession in chairs,
  influence, collaboration, kinship and feuds. Click a scholar to light up their whole academic ancestry and
  descent. A year slider shows the field as of a given year.
- **Chairs** – one timeline per institution: who held which post, from when to when.
- **Map** – where posts were held and where people studied, year by year (press ▶).

Every link taken from the books carries the (German) sentence it rests on.

## Sources

| Source | What it contributes |
|---|---|
| Ernst Windisch, *Geschichte der Sanskrit-Philologie und indischen Altertumskunde* (1917–20) | the bulk: ~1,100 extracted relations |
| Moriz Winternitz, *Geschichte der indischen Litteratur*, vols. 1–3 (1908–20) | ~280 relations, mostly from the introduction (history of Indian studies in Europe) and biographical footnotes |
| Wikidata | identifiers, life dates, portraits, coordinates; dated *employer* / *educated at* / *student of* statements (~730 additional links) |

Current size: 541 scholars, 361 places, 1,773 links.

## How the data was made

1. `pipeline/extract_relations.py` – the OCR text of each book is cut into ~14k-character chunks; Gemini extracts
   relations (`student_of`, `studied_at`, `position_at`, `succeeded`, `collaborated_with`, `influenced_by`,
   `founded`, `other`) as JSON, **using only what the chunk says**, each with a verbatim evidence quote. A relation
   is kept only if its quote is actually found in the chunk (`quote_ok`); ~4% were dropped. Results: `data/chunks/`.
2. `pipeline/resolve_entities.py` – name variants (“R. Roth”, “Rudolph Roth”, “Roth”) and institution names are
   canonicalised with Gemini, batched by surname. Output: `data/graph_books.json` (+ `.graphml`, `edges_books.tsv`).
3. `pipeline/wikidata_enrich.py` – people are matched to Wikidata by name search plus agreement of life dates
   (427 of 541 matched); institutions by name search with Gemini choosing among the candidates. Output: `data/wikidata.json`.
4. `pipeline/merge_sources.py` – merges everything into `docs/data/graph.json`, which the site loads.

### How far to trust the dates

Each year on a link records where it comes from (`ys_src` / `ye_src`):

| value | meaning | share of dated posts |
|---|---|---|
| `text` | stated in Windisch or Winternitz | ~19% |
| `wikidata` | qualifier on a Wikidata statement | ~13% |
| `model` | **recalled by the language model** because neither the books nor Wikidata give a year; kept only at self-reported high/medium confidence | ~68% |

The site marks `model` years with “c.” and a dashed tag/bar. They are usually right for well-known scholars and
should be treated as approximate leads, not as citations. Life dates come from Wikidata where matched, otherwise
from the books or the model.

Other known limits: coverage follows the two books (strongly German-centred, ends c. 1920); teachers named in
the books include philosophers and classicists who are not Indologists; evidence is located by chunk, not by page;
a bar that fades out has no known end.

## Re-running

```bash
pip install google-genai requests networkx
export GEMINI_API_KEY=...            # or put it in ~/code/mitra-evaluation/.secrets.env
export INDOLOGY_OCR_DIR=/path/to/ocr # <docid>.txt files; the OCR texts are not part of this repo
python3 pipeline/extract_relations.py --workers 16   # cached per chunk
python3 pipeline/resolve_entities.py
python3 pipeline/wikidata_enrich.py                  # ~1 request/s, takes a while the first time
python3 pipeline/merge_sources.py
python3 -m http.server -d docs 8000
```

To add a source (e.g. obituaries, Festschriften, Stache-Rosen's *German Indologists*), add its docid to
`SOURCES` in `extract_relations.py` and a short name to `SRC_SHORT` in `resolve_entities.py`, then re-run.

The site is plain HTML + [D3](https://d3js.org) (vendored in `docs/vendor/`), no build step. Land outline from
[world-atlas](https://github.com/topojson/world-atlas) (Natural Earth).
