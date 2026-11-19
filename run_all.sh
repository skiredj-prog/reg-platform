#!/usr/bin/env bash
# Build the projection from the pinned reg-conformance commit, validate it,
# render the badges, and run the guard tests. Fails on the first error.
set -euo pipefail
cd "$(dirname "$0")"
SRC="${REG_CONFORMANCE:-.cache/reg-conformance}"
COMMIT=$(python3 -c "import json;print(json.load(open('source.lock.json'))['commit'])")
REPO=$(python3 -c "import json;print(json.load(open('source.lock.json'))['repository'])")
if [ ! -d "$SRC/.git" ]; then
  git clone -q "$REPO" "$SRC"
fi
git -C "$SRC" fetch -q origin "$COMMIT" 2>/dev/null || git -C "$SRC" fetch -q origin
git -C "$SRC" -c advice.detachedHead=false checkout -q "$COMMIT"
python3 -m reg_platform.project --source "$SRC" --expect-commit "$COMMIT" --out projection/projection-v1.json
python3 -m reg_platform.check_schema projection/projection-v1.json
python3 -m reg_platform.badge --projection projection/projection-v1.json --out badges
REG_CONFORMANCE="$SRC" python3 -m unittest discover -s tests -t . -q
python3 -c "import json;print(\"content_sha256\", json.load(open(\"projection/projection-v1.json\"))[\"content_sha256\"])"
