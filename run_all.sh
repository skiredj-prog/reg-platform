#!/usr/bin/env bash
# Build the projection from the pinned reg-conformance commit, replay the
# reference kernel at that commit, validate the projection, render the
# badges, and run the guard tests. Fails on the first error.
#
# Needs: requirements.txt here, and reg-conformance's requirements.txt (the
# native replay runs its runner.py).
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
mkdir -p .cache
REPORT="$(pwd)/.cache/native-report.json"
rm -f "$REPORT"
# The runner exits 2 when some vectors are implementation gaps; that is a
# result, not a failure of the replay. Only a missing report stops here.
( cd "$SRC" && python3 runner.py --mode native --output "$REPORT" >/dev/null ) || true
[ -s "$REPORT" ] || { echo "native replay produced no report: install reg-conformance's requirements.txt" >&2; exit 1; }
python3 -m reg_platform.project --source "$SRC" --expect-commit "$COMMIT" --expect-tag "$TAG" \
  --native-report "$REPORT" --out projection/projection-v1.json
python3 -m reg_platform.check_schema projection/projection-v1.json
python3 -m reg_platform.badge --projection projection/projection-v1.json --out badges
REG_CONFORMANCE="$SRC" python3 -m unittest discover -s tests -t . -q
python3 -c "import json;print(\"content_sha256\", json.load(open(\"projection/projection-v1.json\"))[\"content_sha256\"])"
