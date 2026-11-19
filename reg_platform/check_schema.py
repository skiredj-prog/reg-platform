"""Validate a projection against contracts/projection-v1.schema.json."""
import json
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
schema = json.loads((ROOT / "contracts/projection-v1.schema.json").read_text())
doc = json.loads(Path(sys.argv[1]).read_text())
jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(doc)
print(f"{sys.argv[1]}: valid against projection-v1 schema")
