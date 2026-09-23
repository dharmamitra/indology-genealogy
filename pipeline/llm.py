#!/usr/bin/env python3
"""One place for the LLM calls of the pipeline, with two backends:

  gemini  google-genai (the default whenever a key is found: $GEMINI_API_KEY or ~/code/mitra-evaluation/.secrets.env)
  claude  the `claude -p` CLI with subscription auth (no API key); chosen with INDOLOGY_LLM=claude or when no key exists

The schemas throughout the pipeline are written in the Gemini style ("OBJECT", "STRING", nullable: true); they are
converted to JSON Schema for the CLI. Answers of the batch steps (resolve, verify, merge) are cached in
data/resolve_cache/ under the model name, so a switch of backend re-asks only what the other backend never answered.
"""
import hashlib, json, os, re, subprocess, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(DATA, "resolve_cache")
GEMINI_MODEL = os.getenv("INDOLOGY_GEMINI_MODEL", "gemini-flash-latest")
CLAUDE_MODEL = os.getenv("INDOLOGY_CLAUDE_MODEL", "sonnet")
SYSTEM = "You are a careful data-extraction and normalisation engine for the history of Indology. Answer only with JSON matching the schema."
LIMIT_RX = re.compile(r"hit your .*?limit.*?resets\s+(.+?)\s*$", re.I | re.S)


def load_key():
    if os.getenv("GEMINI_API_KEY"):
        return os.environ["GEMINI_API_KEY"]
    p = os.path.expanduser("~/code/mitra-evaluation/.secrets.env")
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("No GEMINI_API_KEY found")


def backend():
    b = os.getenv("INDOLOGY_LLM")
    if b:
        return b
    try:
        load_key()
        return "gemini"
    except SystemExit:
        return "claude"


def model_name():
    return GEMINI_MODEL if backend() == "gemini" else "claude:" + CLAUDE_MODEL


def workers(default):
    """Parallelism for the batch steps: Gemini takes dozens of parallel calls, the CLI a handful."""
    return int(os.getenv("INDOLOGY_LLM_WORKERS") or (default if backend() == "gemini" else 6))


def make_client():
    if backend() != "gemini":
        return None
    from google import genai
    return genai.Client(api_key=load_key())


def to_json_schema(s):
    """Gemini-style schema -> JSON Schema (draft 2020) for the Claude CLI."""
    if isinstance(s, list):
        return [to_json_schema(x) for x in s]
    if not isinstance(s, dict):
        return s
    out = {}
    for k, v in s.items():
        if k == "type":
            t = v.lower()
            out["type"] = [t, "null"] if s.get("nullable") else t
        elif k == "nullable":
            continue
        elif k == "properties":
            out[k] = {pk: to_json_schema(pv) for pk, pv in v.items()}
        elif k == "items":
            out[k] = to_json_schema(v)
        else:
            out[k] = v
    return out


def reset_wait(err):
    """Seconds until the subscription window resets, from 'You've hit your session limit · resets 8:50pm (Asia/Tokyo)'."""
    import datetime
    m = LIMIT_RX.search(err)
    if not m:
        return 1800
    txt = m.group(1)
    tm = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", txt, re.I)
    tz = re.search(r"\(([A-Za-z_/]+)\)", txt)
    try:
        from zoneinfo import ZoneInfo
        now = datetime.datetime.now(ZoneInfo(tz.group(1) if tz else "Asia/Tokyo"))
    except Exception:
        return 1800
    if not tm:
        return 1800
    h, mi, ap = int(tm.group(1)), int(tm.group(2) or 0), tm.group(3).lower()
    h = h % 12 + (12 if ap == "pm" else 0)
    t = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    if t <= now:
        t += datetime.timedelta(days=1)
    return min((t - now).total_seconds() + 90, 7 * 24 * 3600)


_pause_until = 0.0


def claude_json(prompt, schema, model=None, timeout=1800):
    """One structured call through the CLI. Returns (data, input_tokens, output_tokens). Waits out usage-limit pauses."""
    global _pause_until
    js = to_json_schema(schema)
    wrapped = js.get("type") == "array"
    if wrapped:  # the CLI wants an object at the top level
        js = {"type": "object", "properties": {"items": js}, "required": ["items"]}
    if not model or not re.match(r"(claude|sonnet|opus|haiku)", model):   # a Gemini model name passed through by a caller
        model = CLAUDE_MODEL
    cmd = ["claude", "-p", "--model", model, "--output-format", "json", "--tools", "", "--json-schema",
           json.dumps(js), "--no-session-persistence", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--setting-sources", "", "--system-prompt", SYSTEM]
    while True:
        d = _pause_until - time.time()
        if d > 0:
            time.sleep(min(d, 60)); continue
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
        try:
            res = json.loads(p.stdout)
        except json.JSONDecodeError:
            raise RuntimeError((p.stderr or p.stdout)[:300])
        data = res.get("structured_output")
        if data is None:
            err = (res.get("result") or p.stderr or "no structured_output")
            if re.search(r"hit your .*limit", err, re.I):
                if _pause_until < time.time():
                    _pause_until = time.time() + reset_wait(err)
                    print(f"[llm] usage limit -> pausing until {time.strftime('%Y-%m-%d %H:%M', time.localtime(_pause_until))}", flush=True)
                continue
            raise RuntimeError(err[:300])
        u = res.get("usage") or {}
        return (data["items"] if wrapped else data), u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0), u.get("output_tokens", 0)


def gemini_json(client, prompt, schema, model=None, thinking=None, max_output_tokens=65536):
    from google.genai import types
    cfg = dict(temperature=0.0, response_mime_type="application/json", response_schema=schema, max_output_tokens=max_output_tokens)
    if thinking is None and os.getenv("INDOLOGY_THINKING"):
        thinking = int(os.environ["INDOLOGY_THINKING"])
    if thinking is not None:
        cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=thinking)
    resp = client.models.generate_content(model=model or GEMINI_MODEL, contents=prompt, config=types.GenerateContentConfig(**cfg))
    um = resp.usage_metadata
    return json.loads(resp.text), um.prompt_token_count or 0, (um.candidates_token_count or 0) + (um.thoughts_token_count or 0)


def generate_json(client, prompt, schema, model=None, thinking=None, max_output_tokens=65536):
    """Uncached structured call on the active backend: (data, in_tokens, out_tokens)."""
    if client is not None:
        return gemini_json(client, prompt, schema, model, thinking, max_output_tokens)
    return claude_json(prompt, schema, model)


def llm_json(client, prompt, schema, retries=5):
    """Cached structured call for the batch steps. A cached answer of either backend is reused."""
    os.makedirs(CACHE, exist_ok=True)
    keys = [os.path.join(CACHE, hashlib.sha1((m + prompt).encode()).hexdigest() + ".json")
            for m in ([model_name()] + [x for x in (GEMINI_MODEL, "claude:" + CLAUDE_MODEL) if x != model_name()])]
    for k in keys:
        if os.path.exists(k):
            return json.load(open(k))
    last = None
    for attempt in range(retries):
        try:
            data, _, _ = generate_json(client, prompt, schema)
            json.dump(data, open(keys[0], "w"), ensure_ascii=False, indent=1)
            return data
        except Exception as e:
            last = e
            time.sleep(min(60, 3 * 2 ** attempt))
    raise RuntimeError(f"llm call failed: {last!r}")


if __name__ == "__main__":
    print(backend(), model_name())
    print(llm_json(make_client(), "Normalise: 'R. Roth', 'Rudolph Roth'", {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "name": {"type": "STRING"}, "canonical": {"type": "STRING"}}, "required": ["name", "canonical"]}}))
