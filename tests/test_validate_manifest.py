# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Agent Software Testing authors
"""Tests of scripts/validate_manifest.py: the example manifest passes, and each kind of break it must report does.

    python -m unittest discover -s tests          or          python -m pytest tests
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import validate_manifest  # noqa: E402

SCHEMA = json.loads((ROOT / "submissions" / "schema.json").read_text(encoding="utf-8"))
EXAMPLE = json.loads((ROOT / "submissions" / "example.json").read_text(encoding="utf-8"))


def errors(document) -> list[str]:
    return validate_manifest.validate(document, SCHEMA)


class ValidateManifest(unittest.TestCase):
    def test_the_example_manifest_is_valid(self):
        self.assertEqual(errors(EXAMPLE), [])
        self.assertEqual(validate_manifest.validate_file(ROOT / "submissions" / "example.json", SCHEMA), [])

    def test_a_missing_required_field_is_reported(self):
        document = copy.deepcopy(EXAMPLE)
        del document["consent"]
        self.assertTrue(any("consent" in e for e in errors(document)), errors(document))

    def test_an_unexpected_field_is_reported(self):
        document = copy.deepcopy(EXAMPLE)
        document["reviewer"] = "someone"
        self.assertTrue(any("reviewer" in e for e in errors(document)), errors(document))



def _mutations(node, path=()):
    """Every way to break the example by one edit: delete a key or an item, or put a value of another kind in its place."""
    yield path, "delete", None
    for replacement in (None, 0, -1, 10**9, "", "x", "0" * 64, [], {}, True):
        yield path, "set", replacement
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _mutations(value, path + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _mutations(value, path + (index,))


def _apply(document, path, action, value):
    document = copy.deepcopy(document)
    parent = document
    for step in path[:-1]:
        parent = parent[step]
    if action == "delete":
        del parent[path[-1]]
    else:
        parent[path[-1]] = value
    return document


class AgreesWithTheSchema(unittest.TestCase):
    def test_it_accepts_and_refuses_exactly_what_a_json_schema_validator_does(self):
        """The script exists so that a submitter needs no package; it must not say yes where the schema says no, or the
        reverse. Compared on every single-edit break of the example."""
        try:
            import jsonschema
        except ImportError:
            self.skipTest("the jsonschema package is the oracle of this test and is not installed")
        oracle = jsonschema.Draft202012Validator(SCHEMA)
        checked = 0
        for path, action, value in _mutations(EXAMPLE):
            if not path:
                continue
            document = _apply(EXAMPLE, path, action, value)
            ours = errors(document) == []
            theirs = oracle.is_valid(document)
            checked += 1
            self.assertEqual(ours, theirs, f"{action} {path} {value!r}: validate_manifest says {ours}, the schema says {theirs}")
        self.assertGreater(checked, 200)

    def test_the_command_line_says_valid_or_not_with_its_exit_status(self):
        import subprocess

        good = ROOT / "submissions" / "example.json"
        done = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "validate_manifest.py"), str(good)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "broken.json"
            document = copy.deepcopy(EXAMPLE)
            del document["consent"]
            broken.write_text(json.dumps(document))
            done = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "validate_manifest.py"), str(good), str(broken)], capture_output=True, text=True)
            self.assertEqual(done.returncode, 1)
            self.assertIn("broken.json", done.stdout)


if __name__ == "__main__":
    unittest.main()
