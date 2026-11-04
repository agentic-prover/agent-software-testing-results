# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Agent Software Testing authors
"""Tests of scripts/package_inputs.py. They use only the standard library.

    python -m unittest discover -s tests          or          python -m pytest tests

The tests write their own bundles. Three environment variables add to them:
  AGENT_SOFTWARE_TESTING_RUNNER         a checkout of the `agent-software-testing` repository (default: the folder `agent-software-testing` next to
                              this one). The validator of the replay tool then checks every part; without it that test
                              is skipped.
  AGENT_SOFTWARE_TESTING_TEST_BUNDLES   a folder of real bundles, read only. The main tests then package these in place of the
                              written ones, with the size limit AGENT_SOFTWARE_TESTING_TEST_MAX_BYTES (default 5 MiB).
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
import random
import sys
import tempfile
import unittest
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import package_inputs  # noqa: E402

RUNNER = Path(os.environ.get("AGENT_SOFTWARE_TESTING_RUNNER", ROOT.parent / "agent-software-testing"))
if (RUNNER / "src" / "agent_software_testing").is_dir():
    sys.path.insert(0, str(RUNNER / "src"))
    from agent_software_testing.validate import validate_bundle
else:
    validate_bundle = None

REAL = os.environ.get("AGENT_SOFTWARE_TESTING_TEST_BUNDLES")
LIMIT = int(os.environ.get("AGENT_SOFTWARE_TESTING_TEST_MAX_BYTES", 5 * 2**20)) if REAL else 40 * 1024
MANIFEST_KEYS = ["program", "entry", "run", "part", "parts", "url", "sha256", "bytes"]


def b64(data):
    return base64.b64encode(data if isinstance(data, bytes) else data.encode()).decode()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def source_sha256(program):
    try:
        return json.loads((RUNNER / "programs" / program / "program.json").read_text())["source"]["sha256"]
    except OSError:
        return "0" * 64


class Bundle:
    """A bundle written by hand: tool.json, cases.jsonl and blobs/."""

    def __init__(self, folder, program, repeat=1, name="Agolic", version="1.0.1", model="gpt-5.5"):
        self.dir, self.cases, self.blobs = Path(folder), [], {}
        self.tool = {
            "schema": "agent-software-testing/tool@0.1",
            "tool": {"name": name, "version": version, "family": "agent", "model": model, "config_sha256": None,
                     "url": None},
            "program": {"id": program, "source_sha256": source_sha256(program)},
            "track": "open", "repeat": repeat, "budget": {"wall_s": 21600}, "notes": "",
        }

    def blob(self, data):
        self.blobs[sha256(data)] = data
        return "sha256:" + sha256(data)

    def case(self, argv=(), stdin=None, files=(), env=None):
        i = len(self.cases) + 1
        self.cases.append({"id": f"{i:06d}", "t_ms": 10 * i, "argv": [b64(a) for a in argv], "stdin": stdin,
                           "files": list(files), "env": env or {}, "cwd": "Lg==", "timeout_ms": 10000,
                           "origin": "test"})

    def file(self, path, data):
        return {"path": b64(path), "kind": "file", "mode": "0644", "blob": self.blob(data)}

    def write(self):
        (self.dir / "blobs").mkdir(parents=True)
        (self.dir / "tool.json").write_text(json.dumps(self.tool, indent=2) + "\n")
        (self.dir / "cases.jsonl").write_text("".join(json.dumps(c, separators=(",", ":")) + "\n" for c in self.cases))
        for digest, data in self.blobs.items():
            (self.dir / "blobs" / digest).write_bytes(data)
        return self.dir


def write_runs(root):
    """Two runs whose blobs are partly compressible and partly random, shared by neighbouring cases."""
    rnd = random.Random(7)
    pool = [bytes([65 + k]) * 3000 for k in range(8)] + [rnd.randbytes(16000) for _ in range(4)]
    krep = Bundle(root / "krep-3", "krep", 3)
    for i in range(120):
        krep.case(["-e", "A"], files=[krep.file("A", pool[i // 10])])
    krep.case()                                                    # a case without any blob
    krep.write()
    gzip = Bundle(root / "gzip-2", "gzip", 2)
    for i in range(60):
        data = pool[i // 5]
        # standard input and the file are one blob
        gzip.case(["-c"], stdin=gzip.blob(data), files=[gzip.file("A", data)], env={"GZIP": b64("-1")})
        gzip.case(["-l", "x.gz"], files=[{"path": b64("d"), "kind": "dir", "mode": "0755"},
                                         {"path": b64("x.gz"), "kind": "symlink", "target": b64("d/y.gz")}])
    gzip.write()
    return root


def package(src, out, *flags):
    """Run main() and return its standard output."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert package_inputs.main([str(src), str(out), *map(str, flags)]) == 0
    return buf.getvalue()


def tree(folder):
    return {p.name: p.read_bytes() for p in sorted(Path(folder).iterdir())}


class PackageInputs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.tmp = Path(tmp.name)
        cls.src = Path(REAL) if REAL else write_runs(cls.tmp / "bundles")
        cls.out, cls.again = cls.tmp / "out", cls.tmp / "again"
        cls.stdout = package(cls.src, cls.out, "--max-bytes", LIMIT)
        package(cls.src, cls.again, "--max-bytes", LIMIT)
        cls.manifest = json.loads((cls.out / "manifest.json").read_text())
        cls.runs = defaultdict(list)                 # (program, run) -> manifest entries by part
        for m in cls.manifest:
            cls.runs[m["program"], m["run"]].append(m)
        cls.sources = {}                             # (program, run) -> bundle folder
        for tool in sorted(cls.src.rglob("tool.json")):
            meta = json.loads(tool.read_text())
            cls.sources[meta["program"]["id"], meta["repeat"]] = tool.parent

    @staticmethod
    def name(m):
        return f"{m['entry']}_{m['program']}_r{m['run']}_part{m['part']}of{m['parts']}.zip"

    def read_part(self, m):
        """The files of a part as {path inside the folder: bytes}; the folder is named like the zip file."""
        with zipfile.ZipFile(self.out / self.name(m)) as z:
            stem = self.name(m)[:-4]
            self.assertTrue(all(n.startswith(stem + "/") for n in z.namelist()))
            return {n[len(stem) + 1:]: z.read(n) for n in z.namelist()}

    def test_manifest(self):
        self.assertEqual(set(self.runs), set(self.sources))
        for m in self.manifest:
            self.assertEqual(list(m), MANIFEST_KEYS)
            self.assertEqual(m["url"], "")
            path = self.out / self.name(m)
            self.assertEqual(m["bytes"], path.stat().st_size)
            self.assertEqual(m["sha256"], sha256(path.read_bytes()))
        for group in self.runs.values():
            self.assertEqual([m["part"] for m in group], list(range(1, len(group) + 1)))
            self.assertEqual({m["parts"] for m in group}, {len(group)})
        zips = sorted(p.name for p in self.out.glob("*.zip"))
        self.assertEqual(zips, sorted(self.name(m) for m in self.manifest))
        self.assertEqual(sorted(self.out.iterdir()), sorted([self.out / "manifest.json", self.out / "SHA256SUMS"]
                                                            + [self.out / z for z in zips]))

    def test_sha256sums(self):
        want = "".join(f"{sha256((self.out / z).read_bytes())}  {z}\n"
                       for z in sorted(p.name for p in self.out.glob("*.zip")))
        self.assertEqual((self.out / "SHA256SUMS").read_text(), want)


    def test_parts_stay_below_the_limit_and_a_run_is_split(self):
        self.assertTrue(all(m["bytes"] <= LIMIT for m in self.manifest))
        self.assertTrue(any(len(group) > 1 for group in self.runs.values()), "no run needed more than one part")

    def test_parts_are_bundles_and_together_the_run(self):
        for key, group in self.runs.items():
            source = self.sources[key]
            parts = [self.read_part(m) for m in group]
            for files in parts:
                self.assertEqual(files["tool.json"], (source / "tool.json").read_bytes())
                needed = {b.removeprefix("sha256:") for line in files["cases.jsonl"].splitlines()
                          for b in package_inputs.blob_names(line)}
                self.assertEqual({n[len("blobs/"):] for n in files if n.startswith("blobs/")}, needed)
                self.assertEqual(set(files), {"tool.json", "cases.jsonl"} | {"blobs/" + b for b in needed})
                for b in needed:
                    self.assertEqual(sha256(files["blobs/" + b]), b)
                    self.assertEqual(files["blobs/" + b], (source / "blobs" / b).read_bytes())
            self.assertEqual(b"".join(files["cases.jsonl"] for files in parts), (source / "cases.jsonl").read_bytes())
            if (source / "cases.jsonl").stat().st_size:
                self.assertTrue(all(files["cases.jsonl"] for files in parts), "a part without a case")

    @unittest.skipUnless(validate_bundle, "needs a checkout of the agent-software-testing repository (AGENT_SOFTWARE_TESTING_RUNNER)")
    def test_parts_pass_the_validator_of_the_replay_tool(self):
        for m in self.manifest:
            folder = self.tmp / "unzipped" / self.name(m)[:-4]
            with zipfile.ZipFile(self.out / self.name(m)) as z:
                z.extractall(folder.parent)
            report = validate_bundle(folder, programs_dir=RUNNER / "programs", strict_budget=True)
            self.assertEqual([(e.where, e.code) for e in report.errors], [], folder.name)
            self.assertFalse({w.code for w in report.warnings} & {"blob.unreferenced", "blob.stray",
                                                                  "bundle.extra_files"})

    def test_same_input_gives_the_same_bytes(self):
        self.assertEqual(tree(self.out), tree(self.again))
        for path in self.out.glob("*.zip"):
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                self.assertEqual(names, sorted(names))
                self.assertEqual({(i.date_time, i.create_system, i.external_attr) for i in z.infolist()},
                                 {(package_inputs.ZIP_TIME, 3, 0o100644 << 16)})

    def test_existing_output_is_refused(self):
        taken = self.tmp / "taken"
        taken.mkdir()
        with self.assertRaises(SystemExit):
            package(self.src, taken)
        self.assertEqual(list(taken.iterdir()), [])
        before = tree(self.out)
        with self.assertRaises(SystemExit):
            package(self.src, self.out)
        self.assertEqual(tree(self.out), before)


class SmallBundles(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def runs(self, programs):
        for n, program in enumerate(programs):
            bundle = Bundle(self.tmp / "bundles" / f"b{n}", program)
            bundle.case(["x"], files=[bundle.file("A", b"data")])
            bundle.write()

    def packaged(self, out):
        return [m["program"] for m in json.loads((out / "manifest.json").read_text())]

    def test_restricted_programs_are_skipped_and_listed(self):
        self.runs(["gawk", "Gcal", "OGGENC", "xorriso", "krep"])
        text = package(self.tmp / "bundles", self.tmp / "out")
        self.assertEqual(self.packaged(self.tmp / "out"), ["krep"])
        self.assertEqual(len(list((self.tmp / "out").glob("*.zip"))), 1)
        lines = text.splitlines()
        self.assertRegex(lines[0], r"^runs 1, parts 1, bytes [0-9,]+, skipped 4$")
        for program in ["gawk", "Gcal", "OGGENC", "xorriso"]:
            self.assertEqual(sum(program in line for line in lines[1:]), 1)
        self.assertFalse(any("krep" in line for line in lines[1:]))

    def test_a_restricted_program_is_matched_whatever_the_case(self):
        self.runs(["krep", "gzip"])
        package(self.tmp / "bundles", self.tmp / "out", "--restricted", "KREP")
        self.assertEqual(self.packaged(self.tmp / "out"), ["gzip"])

    def test_the_default_list_holds_back_every_listed_program(self):
        held = ["bc", "diff", "gawk", "gcal", "jq", "oggenc", "xorriso"]
        self.runs(held + ["krep"])
        text = package(self.tmp / "bundles", self.tmp / "out")
        self.assertEqual(self.packaged(self.tmp / "out"), ["krep"])
        self.assertRegex(text.splitlines()[0], r"skipped %d$" % len(held))

    def test_full_parts_of_incompressible_data_stay_below_the_limit(self):
        rnd, limit = random.Random(3), 10 * 1024
        shared = rnd.randbytes(5000)                    # in every case, so every part holds it once
        bundle = Bundle(self.tmp / "bundles" / "random", "gzip")
        for _ in range(60):
            bundle.case(["-c"], files=[bundle.file("A", rnd.randbytes(500)), bundle.file("B", shared)])
        bundle.write()
        package(self.tmp / "bundles", self.tmp / "out", "--max-bytes", limit)
        sizes = [m["bytes"] for m in json.loads((self.tmp / "out" / "manifest.json").read_text())]
        self.assertGreater(len(sizes), 3)
        self.assertTrue(all(size <= limit for size in sizes), sizes)
        self.assertTrue(all(size >= 0.8 * limit for size in sizes[:-1]), sizes)

    def test_two_bundles_of_one_run_are_refused(self):
        self.runs(["krep"])
        Bundle(self.tmp / "bundles" / "copy", "krep").write()
        with self.assertRaises(SystemExit):
            package(self.tmp / "bundles", self.tmp / "out")
        self.assertFalse((self.tmp / "out").exists())

    def test_a_case_is_never_split_and_an_empty_run_has_one_part(self):
        big = Bundle(self.tmp / "bundles" / "big", "gzip", 1)
        for i in range(3):
            big.case(["-c"], files=[big.file("A", random.Random(i).randbytes(2000))])
        big.write()
        Bundle(self.tmp / "bundles" / "empty", "gzip", 2).write()
        package(self.tmp / "bundles", self.tmp / "out", "--max-bytes", 1)
        manifest = json.loads((self.tmp / "out" / "manifest.json").read_text())
        self.assertEqual([(m["run"], m["part"], m["parts"]) for m in manifest],
                         [(1, 1, 3), (1, 2, 3), (1, 3, 3), (2, 1, 1)])
        [empty] = (self.tmp / "out").glob("*_r2_*.zip")
        with zipfile.ZipFile(empty) as z:
            self.assertEqual(z.read(empty.stem + "/cases.jsonl"), b"")


if __name__ == "__main__":
    unittest.main()
