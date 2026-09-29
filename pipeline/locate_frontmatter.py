#!/usr/bin/env python3
"""Cut the front matter out of the OCR text of every document in a library and write one "packet" per document.

A packet holds the pages that carry biographical statements about the author and the people around them:
title page and imprint, dedication, preface / foreword / acknowledgements (and their equivalents: Vorwort,
avant-propos, はしがき, あとがき, 奥付, prastāvanā, bhūmikā ...), for articles the acknowledgement note.
Nothing else of the book is used, so the extraction stays within what authors say about themselves.

  python3 locate_frontmatter.py --ocr-dir /mnt2/kengo/dox-text/txt --inventory /qnap/kengo/dox/inventory.tsv
  python3 locate_frontmatter.py --worklist data/worklist_kd-dox.jsonl --corpus kd-dox --max-prio 2 \
          --exclude data/covered_by_upstream.txt        # -> extract_prefaces.py --worklist ...

Input:  <ocr-dir>/<relpath>.pdf.txt, pages separated by form feeds (pdftotext / tesseract output).
Output: data/packets/<docid>.txt   pages selected, each headed by "=== p.N ===" (1-based PDF page)
        data/packets/index.tsv     docid, relpath, pages, selected pages, chars, triggers, script, category
        with --worklist: a jsonl in the shape of build_worklist.py (docid, path, corpus, meta, head, windows), one
        window per selected page run, so that extract_prefaces.py consumes it unchanged. The page headers stay in
        the window text; extracted quotes can therefore be located on the PDF page.
Compared with build_worklist.py (marker phrases + 8k windows) this locator works page-wise, recognises headings in
nine languages incl. Sanskrit/Hindi (prastāvanā, bhūmikā) and takes title page, imprint and colophon as well.
The packets (OCR text) are not part of the repository; the extraction results in data/prefaces/ are.
"""
import argparse, csv, hashlib, json, os, re, sys, unicodedata
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, "data", "packets")

MAX_CHARS = 40000          # per packet (~12k tokens)
MAX_PAGE_CHARS = 9000      # a single page is cut after this
BOOK_MIN_PAGES = 60        # shorter documents are treated as articles (also: JSTOR-style first page, Journals folders)
FRONT_SCAN = 45            # pages searched for prefatory headings
TAIL_SCAN = 12             # pages searched for afterwords / colophons
RUN_MAX = 10               # pages taken after a prefatory heading if no stop heading comes first

# Headings are matched on a lower-cased line with all whitespace removed (old books print "P R E F A C E").
START_HEAD = re.compile(
    r"^(?:(?:the|a|an|authors?'?s?|editors?'?s?|translators?'?s?|generaleditors?'?s?|serieseditors?'?s?|publishers?'?s?)?)"
    r"(?:preface|foreword|forword|prefatorynote|prefatoryremarks|acknowledge?ments?|introductorynote|anoteofthanks|"
    r"vorwort|vorrede|vorbemerkung(?:en)?|danksagung|geleitwort|zumgeleit|vorbericht|"
    r"pr[ée]face|avant-?propos|remerciements|avertissement|liminaire|"
    r"prefazione|premessa|ringraziamenti|presentazione|"
    r"voorwoord|woordvooraf|"
    r"pr[oó]logo|agradecimientos|"
    r"предисловие|отавтора|"
    r"はしがき|はじめに|まえがき|序文|序言|緒言|自序|序にかえて|序|あとがき|後記|跋|謝辞|"
    r"प्रस्तावना|भूमिका|प्राक्कथन|उपोद्घात|निवेदन|प्रकाशकीय|सम्पादकीय|संपादकीय|आमुख|पुरोवाक्|दोशब्द|"
    r"prast[aā]van[aā]|bh[uū]mik[aā]|upodgh[aā]ta|"
    r"ভূমিকা|முன்னுரை|ಮುನ್ನುಡಿ|ముందుమాట)"
    r"(?:tothe(?:first|second|third|fourth|fifth|revised|new|english|german|present|reprint|this)?(?:edition|impression|reprint|volume)?|"
    r"zur(?:ersten|zweiten|dritten|vierten|neuen)?auflage|and[a-z]*|(?:tothe|zur|dela|ala)[a-z]*)?[\divxl]*$", re.I)
STOP_HEAD = re.compile(
    r"^(?:contents|tableofcontents|inhalt|inhaltsverzeichnis|inhaltsübersicht|inhaltsubersicht|tabledesmatières|tabledesmatieres|sommaire|"
    r"indice|inhoud|目次|目 次|विषय-?सूची|विषयानुक्रम(?:णी|णिका)?|अनुक्रमणिका|सूची|"
    r"(?:listof)?abbreviations|abkürzungen|abkürzungsverzeichnis|abréviations|sigles|略号|略語|"
    r"introduction|einleitung|einführung|introduzione|introducción|inleiding|序論|序章|緒論|本論|"
    r"chapter[\divxl]*|kapitel[\divxl]*|chapitre[\divxl]*|capitolo[\divxl]*|第[一二三四五六七八九十\d]+章|"
    r"part[\divxl]*|teil[\divxl]*|partie[\divxl]*|book[\divxl]*|"
    r"bibliography|bibliographie|literaturverzeichnis|index|indices|register|参考文献|索引|"
    r"text|texte|translation|übersetzung|notes|anmerkungen)"
    r"[.:：\-–—]*$", re.I)
CONTENTS_LIKE = re.compile(r"(\.{6,}|\s\d{1,3}\s*$)", re.M)   # dotted leaders / trailing page numbers

# Sentences of thanks, dedication, supervision: catch preface pages whose heading was lost in OCR,
# and acknowledgement notes in articles.
THANKS = re.compile(
    r"\b(I (?:am|feel) (?:deeply |most |very |especially |particularly |greatly |much )?(?:grateful|indebted|obliged)|"
    r"my (?:sincere |heartfelt |deep(?:est)? |warm(?:est)? |special |profound )?(?:thanks|gratitude)|"
    r"(?:I|we) (?:wish|would like|want|should like) to (?:thank|express|acknowledge|record)|"
    r"(?:I|we) (?:also |must |gladly |gratefully )?(?:thank|owe)|thanks are (?:also )?due|"
    r"(?:doctoral|ph\.? ?d\.?|d\.phil\.?|habilitation) (?:dissertation|thesis)|(?:my|the) (?:doctoral )?(?:supervisor|dissertation advisor|advisor|adviser|doktorvater|doktormutter|teacher|guru)|"
    r"(?:revised|slightly revised|expanded) version of (?:my|a|the) (?:doctoral |ph\.? ?d\.? )?(?:dissertation|thesis)|"
    r"(?:ich|wir) (?:danke|möchte[n]? .{0,40}danken|habe[n]? .{0,30}zu danken)|(?:mein|meinem|meiner) (?:verehrten |hochverehrten )?(?:lehrer|doktorvater|doktormutter)|dank (?:schulde|gebührt)|zu (?:großem |besonderem )?dank verpflichtet|"
    r"(?:je|nous) (?:tiens|tenons|voudrais|voudrions|remercie|remercions|dois|devons)|ma (?:reconnaissance|gratitude)|mon (?:maître|directeur)|"
    r"ringrazi|mio maestro|"
    r"dedicated to|gewidmet|dédié|in memoriam|zum andenken|dem andenken|to the memory of)\b", re.I)
THANKS_NONLATIN = re.compile(
    r"(感謝|謝意|御礼|お礼|恩師|指導教官|指導教授|学位論文|博士論文|に捧げ|を捧げ|ご教示|御教示|ご指導|御指導|ご助言|"
    r"कृतज्ञ|आभार|धन्यवाद|गुरु(?:वर|देव|चरण)|गुरुजी|कृपा|साहाय्य|सहायता)")


def thanks_hits(page):
    return len(THANKS.findall(page)) + len(THANKS_NONLATIN.findall(page))
COLOPHON = re.compile(r"(発行所|発行者|発行人|印刷所|著者略歴|著者紹介|訳者紹介|編者紹介|奥付|©|ISBN|about the author|zum autor|notes on (?:the )?(?:contributors|authors)|प्रकाशक|मुद्रक|प्रथम संस्करण|संस्करण)", re.I)
ARTICLE_NOTE = re.compile(r"(^\s*[\*†¹]\s|an earlier (?:version|draft) of this|this (?:paper|article|essay|study) (?:is|was|grew|originated)|"
                          r"presented at|read at|thanks?|grateful|acknowledg|dank|remerci|感謝|謝辞|付記|補記)", re.I | re.M)

SCRIPTS = (("cjk", r"[぀-ヿ一-鿿]"), ("deva", r"[ऀ-ॿ]"), ("beng", r"[ঀ-৿]"),
           ("cyr", r"[Ѐ-ӿ]"), ("latin", r"[A-Za-z]"))
SCRIPT_RX = [(k, re.compile(v)) for k, v in SCRIPTS]


def docid_for(relpath):
    base = os.path.splitext(os.path.basename(relpath))[0]
    slug = unicodedata.normalize("NFKC", base)
    slug = re.sub(r"[^\w\-]+", "_", slug).strip("_")[:60]
    return hashlib.sha1(relpath.encode()).hexdigest()[:8] + "__" + slug


DECOR = r"[।॥\s\*＊■□◆●○◇—–\-_=~:：.,;()\[\]【】「」『』〔〕|<>/\\'\"“”‘’]+"


def compact(line):
    """lower-case, no whitespace, decorations stripped at both ends: '＊＊＊ 謝 辞 ＊＊＊' -> '謝辞'."""
    s = re.sub("^" + DECOR + "|" + DECOR + "$", "", line)
    return re.sub(r"\s+", "", s).lower()


def heading_kind(page):
    """Return ('start'|'stop', line) for the first prefatory/stop heading on the page, else None."""
    for ln in page.splitlines()[:25]:            # headings sit near the top of a page
        s = ln.strip()
        if not s or len(s) > 60:
            continue
        c = compact(s)
        if not (1 <= len(c) <= 45):
            continue
        if START_HEAD.match(c):
            return "start", s
        if STOP_HEAD.match(c):
            return "stop", s
    return None


def script_of(text):
    counts = {k: len(rx.findall(text[:200000])) for k, rx in SCRIPT_RX}
    lat = counts.pop("latin")
    best = max(counts, key=counts.get)
    return best if counts[best] > max(200, lat * 0.15) else "latin"


def clean_page(p):
    p = re.sub(r"[ \t]{3,}", "  ", p)
    p = re.sub(r"\n{3,}", "\n\n", p)
    return p.strip("\n")


def alnum_ratio(s):
    if not s:
        return 0.0
    return sum(ch.isalnum() for ch in s) / len(s)


def select_pages(pages, script):
    """Return {page_index: (priority, trigger)}; lower priority number = kept first when trimming."""
    n = len(pages)
    sel = {}

    def add(i, pri, trig):
        if 0 <= i < n and (i not in sel or sel[i][0] > pri):
            sel[i] = (pri, trig)

    head = "\n".join(pages[:2])
    is_article = n < BOOK_MIN_PAGES or bool(re.search(r"(Author\(s\):|Stable URL|Source:.*(Journal|Bulletin|Zeitschrift|Studies)|JSTOR)", head))
    if is_article:
        add(0, 0, "title"); add(1, 1, "title")
        add(n - 1, 3, "end")
        for i, p in enumerate(pages):
            if ARTICLE_NOTE.search(p) and thanks_hits(p):
                add(i, 1, "thanks")
        return sel

    got = 0
    for i in range(min(12, n)):
        if len(pages[i].strip()) >= 80 or i == 0:
            add(i, 1, "title"); got += 1
        if got == 4:
            break
    # prefatory headings in the front matter, followed by their run of pages
    i = 0
    scan_to = min(FRONT_SCAN, n)
    while i < scan_to:
        hk = heading_kind(pages[i])
        if hk and hk[0] == "start":
            add(i, 0, "head:" + hk[1][:20])
            j = i + 1
            while j < min(i + RUN_MAX, n):
                h2 = heading_kind(pages[j])
                if h2 and h2[0] == "stop":
                    break
                if h2 and h2[0] == "start":
                    break                       # next heading starts its own run
                if len(CONTENTS_LIKE.findall(pages[j])) > 12:
                    break                       # a contents page without heading
                add(j, 0, "run")
                j += 1
            i = j
            continue
        i += 1
    # pages of thanks without a recognised heading
    cand = sorted(((thanks_hits(pages[i]), -i) for i in range(min(FRONT_SCAN, n))), reverse=True)
    for h, negi in cand[:3]:
        if h >= 2:
            add(-negi, 2, "thanks"); add(-negi + 1, 2, "thanks+1")
    # back matter: afterword (Japanese books), colophon, author note
    for i in range(max(0, n - TAIL_SCAN), n):
        hk = heading_kind(pages[i])
        if hk and hk[0] == "start":
            add(i, 1, "tail-head:" + hk[1][:20])
            for j in range(i + 1, min(i + 5, n)):
                if heading_kind(pages[j]):
                    break
                add(j, 1, "tail-run")
        elif script == "cjk" and thanks_hits(pages[i]) >= 2:
            add(i, 2, "tail-thanks")     # あとがき without a recognised heading; Western books thank at the front
    for i in range(max(0, n - 3), n):
        if COLOPHON.search(pages[i]) or (script == "cjk" and len(pages[i].strip()) < 900 and re.search(r"(発行|印刷|著者)", pages[i])):
            add(i, 2, "colophon")
    return sel


def build(args):
    path, relpath, ocr_dir = args
    try:
        with open(path, errors="ignore") as f:
            text = f.read()
    except OSError as e:
        return relpath, None, f"unreadable: {e}"
    if len(text.strip()) < 200:
        return relpath, None, "empty"
    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    script = script_of(text)
    sel = select_pages(pages, script)
    if not sel:
        return relpath, None, "nothing selected"
    # trim to MAX_CHARS by priority, then page order
    order = sorted(sel, key=lambda i: (sel[i][0], i))
    kept, total = [], 0
    for i in order:
        p = clean_page(pages[i])[:MAX_PAGE_CHARS]
        if alnum_ratio(p) < 0.25 and sel[i][1] not in ("title",):
            continue
        if total + len(p) > MAX_CHARS and kept:
            continue
        kept.append(i); total += len(p)
    kept.sort()
    if not kept:
        return relpath, None, "garbage"
    body = "\n\n".join(f"=== p.{i + 1} ===\n{clean_page(pages[i])[:MAX_PAGE_CHARS]}" for i in kept)
    docid = docid_for(relpath)
    with open(os.path.join(OUT_DIR, docid + ".txt"), "w") as f:
        f.write(body)
    trig = Counter(sel[i][1].split(":")[0] for i in kept)
    return relpath, dict(docid=docid, pages=len(pages), selected=len(kept), chars=len(body), script=script,
                         triggers=";".join(f"{k}={v}" for k, v in sorted(trig.items())),
                         page_list=",".join(str(i + 1) for i in kept)), None


def prio(r):
    t = {x.split("=")[0] for x in r["triggers"].split(";")}
    book = int(r["pages"]) >= BOOK_MIN_PAGES
    if book and ("head" in t or "tail-head" in t):
        return 0
    if book and "thanks" in t:
        return 1
    if not book and "thanks" in t:
        return 2
    return 3 if book else 4


def write_worklist(rows, args):
    """Packets -> worklist records (build_worklist.py shape). Consecutive selected pages form one window; the
    'start' offset is the character position of the window's first page in the full text."""
    ex = {l.strip() for l in open(args.exclude)} if args.exclude else set()
    n, nw = 0, 0
    with open(args.worklist, "w", encoding="utf-8") as out:
        for r in sorted(rows, key=lambda r: (prio(r), r["relpath"])):
            if prio(r) > args.max_prio or r["docid"] in ex:
                continue
            path = os.path.join(args.ocr_dir, r["relpath"] + ".txt")
            with open(path, errors="ignore") as f:
                text = f.read()
            pages = text.split("\f")
            offs, o = [], 0
            for p in pages:
                offs.append(o); o += len(p) + 1
            packet = open(os.path.join(OUT_DIR, r["docid"] + ".txt"), encoding="utf-8").read()
            blocks = re.split(r"(?m)^(?==== p\.\d+ ===$)", packet)
            wins, cur = [], None
            for b in blocks:
                m = re.match(r"=== p\.(\d+) ===", b)
                if not m:
                    continue
                pg = int(m.group(1))
                if cur and pg == cur["last"] + 1 and len(cur["text"]) + len(b) <= 40000:
                    cur["text"] += "\n" + b; cur["last"] = pg
                else:
                    cur = {"where": "back" if pg > max(4, len(pages) - TAIL_SCAN) else "front", "start": offs[pg - 1] if pg - 1 < len(offs) else 0,
                           "score": 0, "text": b, "last": pg}
                    wins.append(cur)
            for w in wins:
                w["score"] = len(THANKS.findall(w["text"])) + len(THANKS_NONLATIN.findall(w["text"])) + (5 if heading_kind(w["text"]) else 0)
                del w["last"]
            rec = {"docid": os.path.splitext(os.path.basename(r["relpath"]))[0], "path": path, "corpus": args.corpus,
                   "meta": {"category": r["category"], "pdf_pages": int(r["pages"]), "script": r["script"], "kd_docid": r["docid"]},
                   "head": re.sub(r"\s+", " ", text[:700]), "windows": wins}
            out.write(json.dumps(rec, ensure_ascii=False) + "\n"); n += 1; nw += len(wins)
    print(f"[locate] worklist {args.worklist}: {n} documents, {nw} windows")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ocr-dir", default=os.getenv("INDOLOGY_OCR_DIR", "/mnt2/kengo/dox-text/txt"))
    ap.add_argument("--inventory", default="/qnap/kengo/dox/inventory.tsv", help="TSV with path/category/pdf_pages (optional)")
    ap.add_argument("--only", help="substring filter on relpath")
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--worklist", help="also write a build_worklist.py-shaped jsonl for extract_prefaces.py")
    ap.add_argument("--corpus", default="kd-dox")
    ap.add_argument("--max-prio", type=int, default=4, help="worklist only: 0 books with a preface heading, 1 + books with "
                    "thanks, 2 + articles with thanks, 3 + other books, 4 everything")
    ap.add_argument("--exclude", help="worklist only: file with docids to leave out")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    cat = {}
    if args.inventory and os.path.exists(args.inventory):
        for r in csv.DictReader(open(args.inventory, encoding="utf-8", errors="ignore"), delimiter="\t"):
            cat["Books_and_Articles_on_iCloud/" + r["path"]] = r.get("category", "")
    tasks = []
    for d, _, fs in os.walk(args.ocr_dir):
        for fn in fs:
            if not fn.endswith(".txt"):
                continue
            p = os.path.join(d, fn)
            rel = os.path.relpath(p, args.ocr_dir)[:-4]        # strip ".txt" -> "<relpath>.pdf"
            if args.only and args.only not in rel:
                continue
            tasks.append((p, rel, args.ocr_dir))
    tasks.sort()
    print(f"[locate] {len(tasks)} texts", flush=True)
    rows, skipped = [], Counter()
    with ProcessPoolExecutor(args.workers) as ex:
        for rel, info, why in ex.map(build, tasks, chunksize=40):
            if info is None:
                skipped[why.split(":")[0]] += 1; continue
            info["relpath"] = rel
            info["category"] = cat.get(rel, rel.split("/")[0])
            rows.append(info)
    cols = ["docid", "relpath", "category", "pages", "selected", "chars", "script", "triggers", "page_list"]
    with open(os.path.join(OUT_DIR, "index.tsv"), "w") as f:
        f.write("\t".join(cols) + "\n")
        for r in sorted(rows, key=lambda r: r["relpath"]):
            f.write("\t".join(str(r[c]) for c in cols) + "\n")
    if args.worklist:
        write_worklist(rows, args)
    tc = Counter()
    for r in rows:
        for t in r["triggers"].split(";"):
            tc[t.split("=")[0]] += 1
    print(f"[locate] {len(rows)} packets, {sum(r['chars'] for r in rows)/1e6:.1f} M chars; skipped {dict(skipped)}")
    print(f"[locate] documents with trigger: {dict(tc)}")
    print(f"[locate] scripts: {dict(Counter(r['script'] for r in rows))}")


if __name__ == "__main__":
    main()
