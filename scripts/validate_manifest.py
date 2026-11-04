#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Agent Software Testing authors
"""Validate a submission manifest against submissions/schema.json, with the standard library only.

    python scripts/validate_manifest.py submissions/example.json [MORE.json ...]

With no argument it validates every manifest in submissions/ except schema.json itself. The schema is Draft 2020-12;
this checks exactly the keywords the schema uses (type, required, properties, additionalProperties, items, minItems,
const, enum, pattern, minLength, maxLength, minimum, maximum, exclusiveMinimum, oneOf and $ref), so a manifest that
passes is structurally correct. It is not an acceptance: the rules the schema cannot express are in
submissions/README.md.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "submissions" / "schema.json"


def _is_type(value, kind: str) -> bool:
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "null":
        return value is None
    return True


def validate(instance, schema: dict, root: dict | None = None, path: str = "$") -> list[str]:
    """The ways ``instance`` breaks ``schema``, as "address: problem" strings; an empty list means it conforms."""

    root = schema if root is None else root
    errors: list[str] = []
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            return [f"{path}: unsupported $ref {ref!r}"]
        target = root
        for part in ref[2:].split("/"):
            target = target[part]
        return validate(instance, target, root, path)
    if "oneOf" in schema:
        passing = [s for s in schema["oneOf"] if not validate(instance, s, root, path)]
        if len(passing) != 1:
            errors.append(f"{path}: matches {len(passing)} of the oneOf alternatives, not exactly one")
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: is not {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} is not one of {schema['enum']!r}")
    kinds = schema.get("type")
    if kinds is not None:
        kinds = [kinds] if isinstance(kinds, str) else kinds
        if not any(_is_type(instance, k) for k in kinds):
            errors.append(f"{path}: is not of type {'/'.join(kinds)}")
            return errors
    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            errors.append(f"{path}: is shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: is longer than {schema['maxLength']} characters")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            errors.append(f"{path}: does not match {schema['pattern']!r}")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: is below {schema['minimum']}")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: is not above {schema['exclusiveMinimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: is above {schema['maximum']}")
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: is missing {key!r}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            if key in properties:
                errors.extend(validate(value, properties[key], root, f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: has the unexpected key {key!r}")
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: has fewer than {schema['minItems']} entries")
        if "items" in schema:
            for index, item in enumerate(instance):
                errors.extend(validate(item, schema["items"], root, f"{path}[{index}]"))
    return errors


def validate_file(path: Path, schema: dict | None = None) -> list[str]:
    schema = schema if schema is not None else json.loads(SCHEMA.read_text(encoding="utf-8"))
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [str(exc)]
    return validate(document, schema)


def main(argv: list[str]) -> int:
    paths = [Path(name) for name in argv[1:]]
    if not paths:
        paths = sorted(p for p in (ROOT / "submissions").glob("*.json") if p.name != "schema.json")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    failed = 0
    for path in paths:
        errors = validate_file(path, schema)
        for error in errors:
            print(f"{path}: {error}")
        if errors:
            failed += 1
        else:
            print(f"{path}: valid")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
