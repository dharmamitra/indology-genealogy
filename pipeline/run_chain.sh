#!/bin/bash
# after extraction: verify new records, refresh the prior, resolve, wikidata, merge
set -o pipefail
cd "$(dirname "$0")"
export INDOLOGY_THINKING=0
export INDOLOGY_LLM_WORKERS=12
echo "== verify $(date)";  python3 verify.py 2>&1 | grep -v 'automatic function calling'
echo "== prior $(date)";   python3 build_resolve_prior.py
echo "== resolve $(date)"; python3 resolve_all.py 2>&1 | grep -v 'automatic function calling'
echo "== wikidata $(date)"; python3 wikidata_all.py 2>&1 | tail -20
echo "== merge $(date)";   python3 merge_all.py 2>&1 | grep -v 'automatic function calling'
echo "== CHAIN-DONE $(date)"
