#!/usr/bin/env bash
# Build the projection from the pinned reg-conformance commit, validate it,
# render the badges, and run the guard tests. Fails on the first error.
set -euo pipefail
cd "$(dirname "$0")"
SRC="${REG_CONFORMANCE:-.cache/reg-conformance}"
lock() { python3 -c "import json,sys;v=json.load(open('source.lock.json')).get(sys.argv[1]);print(v or '')" "$1"; }
COMMIT=$(lock commit)
TAG=$(lock tag)
REPO=$(lock repository)
if [ ! -d "$SRC/.git" ]; then
  git clone -q "$REPO" "$SRC"
fi
if ! git -C "$SRC" cat-file -e "$COMMIT^{commit}" 2>/dev/null; then
  git -C "$SRC" fetch -q origin "$COMMIT" 2>/dev/null || git -C "$SRC" fetch -q origin
fi
if [ -n "$TAG" ] && ! git -C "$SRC" rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  git -C "$SRC" fetch -q origin "refs/tags/$TAG:refs/tags/$TAG"
fi
git -C "$SRC" -c advice.detachedHead=false checkout -q "$COMMIT"
python3 -m reg_platform.project --source "$SRC" --expect-commit "$COMMIT" --expect-tag "$TAG" --out projection/projection-v1.json
python3 -m reg_platform.check_schema projection/projection-v1.json
python3 -m reg_platform.badge --projection projection/projection-v1.json --out badges
REG_CONFORMANCE="$SRC" python3 -m unittest discover -s tests -t . -q
python3 -c "import json;print(\"content_sha256\", json.load(open(\"projection/projection-v1.json\"))[\"content_sha256\"])"
