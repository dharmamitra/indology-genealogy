#!/usr/bin/env python3
"""Crowd-sourced corrections by email: read suggestion mails, parse them with Gemini into editorial lines, open a PR.

Mails to dharmamitra.project@gmail.com whose subject starts with "[Indology Lineages]" (the site's suggest link) or
that mention the project are fetched through the Gmail API. Gemini turns each mail into rows of
data/manual/relations.tsv (subject, type, object, role, years, source, contributor = sender's name + address domain),
or into merge lines (two entries are the same person), or rejects it (spam, off-topic, no checkable content).
Accepted rows go to a branch `suggest/<message id>` and a pull request quoting the mail; the sender gets a reply with
the link. A maintainer merges; the nightly rebuild takes the lines in.

Setup (once): a Gmail API OAuth client (credentials.json) for the account, then `python3 inbox_to_pr.py --auth`
prints a link to approve; the token is kept in data/gmail_token.json (git-ignored). Cron: hourly `inbox_to_pr.py`.
"""
import argparse, base64, email, email.utils, json, os, re, subprocess, sys, time
from email.mime.text import MIMEText

from google import genai
from google.genai import types

from extract_relations import load_key, DATA, ROOT, REL_TYPES

TOKEN = os.path.join(DATA, "gmail_token.json")
CREDS = os.path.expanduser("~/code/mitra-evaluation/gmail_credentials.json")
SEEN = os.path.join(DATA, "inbox_seen.json")
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
TSV = os.path.join(DATA, "manual", "relations.tsv")
MERGES = os.path.join(DATA, "manual", "merges.tsv")

PROMPT = """You maintain an academic-genealogy database of Indology, Buddhist studies and Tibetology (scholars, their teachers, \
students, posts, studies). Below is an email sent through the site's "suggest a correction" link (or written freely). \
Turn it into database operations. Relation types: {types}. Roles for student_of: "doctoral supervisor", "teacher", \
"MA supervisor", "habilitation", "traditional teacher". Years only if the mail states them.

Return:
- "rows": relations to ADD, each {{subject, type, object, role, year_start, year_end, source}} — "source" is the mail's
  citation (a publication, a web page, a CV) or "personal knowledge of the contributor" if none is given.
- "retract": relations the mail says are WRONG, same shape (they are recorded as retractions and removed from the graph).
- "merges": pairs of names the mail says are the same person, [{{keep, drop}}].
- "contributor": the sender's name as signed in the mail, else the display name of the address.
- "reply": two or three sentences to the sender, plain and friendly, stating what was recorded and that it goes live after review.
- "verdict": "accept" if at least one operation was recorded; "reject" for spam, advertising, off-topic or empty mails; \
"unclear" if the mail is about the project but you cannot tell what to record — then "reply" asks the one question needed.
Use names exactly as the mail gives them. Do not invent facts, years or sources.

SUBJECT: {subject}
FROM: {sender}
BODY:
{body}"""
SCHEMA = {"type": "OBJECT", "properties": {
    "verdict": {"type": "STRING", "enum": ["accept", "reject", "unclear"]}, "contributor": {"type": "STRING"}, "reply": {"type": "STRING"},
    "rows": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"subject": {"type": "STRING"}, "type": {"type": "STRING", "enum": REL_TYPES}, "object": {"type": "STRING"},
                                                                          "role": {"type": "STRING", "nullable": True}, "year_start": {"type": "INTEGER", "nullable": True}, "year_end": {"type": "INTEGER", "nullable": True}, "source": {"type": "STRING"}},
                                          "required": ["subject", "type", "object", "source"]}},
    "retract": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"subject": {"type": "STRING"}, "type": {"type": "STRING", "enum": REL_TYPES}, "object": {"type": "STRING"}, "source": {"type": "STRING"}}, "required": ["subject", "type", "object"]}},
    "merges": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"keep": {"type": "STRING"}, "drop": {"type": "STRING"}}, "required": ["keep", "drop"]}}},
    "required": ["verdict", "contributor", "reply", "rows", "retract", "merges"]}


def gmail():
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(TOKEN, SCOPES) if os.path.exists(TOKEN) else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDS, SCOPES)
            creds = flow.run_local_server(port=0, open_browser=False)
        open(TOKEN, "w").write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def body_of(msg):
    def walk(part):
        if part.is_multipart():
            for p in part.get_payload():
                t = walk(p)
                if t:
                    return t
        elif part.get_content_type() == "text/plain":
            return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        elif part.get_content_type() == "text/html":
            import html as h
            return h.unescape(re.sub(r"<[^>]+>", " ", part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")))
        return ""
    return walk(msg)


def tsv_line(r, contributor):
    return "\t".join(str(x or "").replace("\t", " ").replace("\n", " ") for x in
                     [r["subject"], r["type"], r["object"], r.get("role"), r.get("year_start"), r.get("year_end"), r["source"], contributor])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--auth", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="parse and print, change nothing")
    ap.add_argument("--file", help="parse one .eml file instead of the inbox (for tests)")
    args = ap.parse_args()
    client = genai.Client(api_key=load_key())

    def parse(subject, sender, body):
        r = client.models.generate_content(model="gemini-flash-latest", contents=PROMPT.format(types=", ".join(REL_TYPES), subject=subject, sender=sender, body=body[:8000]),
                                           config=types.GenerateContentConfig(temperature=0.0, response_mime_type="application/json", response_schema=SCHEMA))
        return json.loads(r.text)

    if args.file:
        msg = email.message_from_bytes(open(args.file, "rb").read())
        print(json.dumps(parse(msg["Subject"] or "", msg["From"] or "", body_of(msg)), ensure_ascii=False, indent=1)); return
    svc = gmail()
    if args.auth:
        print("Gmail authorised; token stored in", TOKEN); return
    seen = set(json.load(open(SEEN))) if os.path.exists(SEEN) else set()
    res = svc.users().messages().list(userId="me", q='subject:"[Indology Lineages]" OR "indology-genealogy" OR "Indology Lineages"', maxResults=50).execute()
    for m in res.get("messages", []):
        if m["id"] in seen:
            continue
        raw = svc.users().messages().get(userId="me", id=m["id"], format="raw").execute()
        msg = email.message_from_bytes(base64.urlsafe_b64decode(raw["raw"]))
        subject, sender, body = msg["Subject"] or "", msg["From"] or "", body_of(msg)
        if "dharmamitra.project@gmail.com" in sender:  # our own replies
            seen.add(m["id"]); continue
        name, addr = email.utils.parseaddr(sender)
        out = parse(subject, sender, body)
        contributor = f'{out.get("contributor") or name or addr.split("@")[0]} <{addr.split("@")[0][:3]}…@{addr.split("@")[-1]}>'
        print(f"[inbox] {m['id']} {addr}: {out['verdict']} rows={len(out['rows'])} retract={len(out['retract'])} merges={len(out['merges'])}", flush=True)
        if args.dry_run:
            print(json.dumps(out, ensure_ascii=False, indent=1)); continue
        if out["verdict"] == "accept" and (out["rows"] or out["retract"] or out["merges"]):
            branch = f"suggest/{m['id'][:12]}"
            subprocess.run(["git", "checkout", "-q", "-b", branch, "main"], cwd=ROOT, check=True)
            with open(TSV, "a", encoding="utf-8") as f:
                for r in out["rows"]:
                    f.write(tsv_line(r, contributor) + "\n")
                for r in out["retract"]:
                    f.write("#RETRACT\t" + tsv_line({**r, "source": r.get("source") or "per contributor"}, contributor) + "\n")
            if out["merges"]:
                with open(MERGES, "a", encoding="utf-8") as f:
                    for mg in out["merges"]:
                        f.write(f'{mg["keep"]}\t{mg["drop"]}\t# {contributor}\n')
            subprocess.run(["git", "add", TSV, MERGES], cwd=ROOT, check=True)
            subprocess.run(["git", "commit", "-q", "-m", f"Suggestion by email: {subject[:60]}\n\nContributor: {contributor}"], cwd=ROOT, check=True)
            subprocess.run(["git", "push", "-q", "-u", "origin", branch], cwd=ROOT, check=True)
            quoted = "\n".join("> " + l for l in body.strip().splitlines()[:60])
            pr = subprocess.run(["gh", "pr", "create", "--title", f"Suggestion: {subject[:70]}", "--body",
                                 f"Sent by {contributor} on {msg['Date']}.\n\nRecorded lines:\n```\n" + "\n".join(tsv_line(r, contributor) for r in out["rows"]) + "\n```\n"
                                 + (f"Retractions: {len(out['retract'])}\n" if out["retract"] else "") + (f"Merges: {out['merges']}\n" if out["merges"] else "")
                                 + f"\nOriginal mail:\n{quoted}\n\n(automatically parsed by pipeline/inbox_to_pr.py; review before merging)"],
                                cwd=ROOT, capture_output=True, text=True).stdout.strip()
            subprocess.run(["git", "checkout", "-q", "main"], cwd=ROOT, check=True)
            reply = out["reply"] + (f"\n\nThe proposed change is recorded here for review: {pr}" if pr else "")
        else:
            reply = out["reply"] if out["verdict"] == "unclear" else None
        if reply:
            mt = MIMEText(reply + "\n\n— Indology Lineages, https://dharmamitra.github.io/indology-genealogy/")
            mt["To"], mt["Subject"], mt["In-Reply-To"] = sender, "Re: " + subject, msg["Message-ID"] or ""
            svc.users().messages().send(userId="me", body={"raw": base64.urlsafe_b64encode(mt.as_bytes()).decode(), "threadId": m.get("threadId")}).execute()
        seen.add(m["id"])
        json.dump(sorted(seen), open(SEEN, "w"))


if __name__ == "__main__":
    main()
