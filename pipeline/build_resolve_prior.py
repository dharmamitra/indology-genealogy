#!/usr/bin/env python3
"""data/resolve_cache/*.json -> data/resolve_prior.json: every name / institution / romanisation answer the resolver
has ever received, keyed by the input string. resolve_all.py reuses them instead of asking again, so canonical
identities survive re-runs, new corpora and a change of model. Re-run after each resolve_all.py run."""
import glob, json, os

from extract_relations import DATA


def main():
    prior, n = {"people": {}, "institutions": {}, "roman": {}}, 0
    for f in sorted(glob.glob(os.path.join(DATA, "resolve_cache", "*.json"))):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if not isinstance(d, list) or not d or not isinstance(d[0], dict) or "name" not in d[0]:
            continue
        x = d[0]
        tgt = ("roman" if "family" in x else "institutions" if "city" in x and "canonical" in x
               else "people" if "kind" in x and "canonical" in x else None)
        if not tgt:
            continue
        for r in d:
            if r.get("name"):
                prior[tgt].setdefault(r["name"], r)
        n += 1
    json.dump(prior, open(os.path.join(DATA, "resolve_prior.json"), "w"), ensure_ascii=False)
    print(f"[prior] {n} cache files -> {', '.join(f'{len(v)} {k}' for k, v in prior.items())}")


if __name__ == "__main__":
    main()
