# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Agent Software Testing authors
"""Tests of scripts/replay_job.py with made-up data: the failure paths of the replay workflow without GitHub or Docker.

    python -m unittest discover -s tests          or          python -m pytest tests

The tests check codes, states and files. They do not pin the wording of messages. If the environment variable
REPLAY_REAL_OUTPUT names a folder with real runner outputs (gzip-r1/replay.json and so on), one more test replays
them through the happy path.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import replay_job as rj  # noqa: E402

EXAMPLE = json.loads((ROOT / "submissions" / "example.json").read_text(encoding="utf-8"))
SHA = "ab" * 32
HEAD = "c" * 40
NO_SLEEP = lambda seconds: None  # noqa: E731
SECRET = "SECRET-FRAME-xyz"


def make_root(tmp: Path, pins: bool = True) -> Path:
    root = tmp / "root"
    (root / ".github").mkdir(parents=True)
    if pins:
        (root / ".github" / "replay-pins.json").write_text(json.dumps({
            "runner_repository": "example-org/runner", "runner_ref": "d" * 40,
            "image": "ghcr.io/example-org/replay@sha256:" + "e" * 64}))
    (root / "programs.csv").write_text("program,name\ngzip,gzip\nsed,sed\n")
    return root


def tool_json(program="gzip", repeat=1, track="open", name="example-generator", version="1.0") -> dict:
    return {"schema": "agent-software-testing/tool@0.1", "tool": {"name": name, "version": version, "family": "fuzzer"},
            "program": {"id": program, "source_sha256": "0" * 64}, "track": track, "repeat": repeat}


def tar_bytes(entries: list[tuple[str, bytes | None, str]]) -> bytes:
    """entries: (name, content, kind) with kind file, dir, symlink or hardlink."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content, kind in entries:
            info = tarfile.TarInfo(name)
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                tar.addfile(info)
            else:
                info.size = len(content or b"")
                tar.addfile(info, io.BytesIO(content or b""))
    return buf.getvalue()


def good_bundle(**kw) -> bytes:
    return tar_bytes([("tool.json", json.dumps(tool_json(**kw)).encode(), "file"), ("cases.jsonl", b"", "file"),
                      ("blobs", None, "dir"), ("blobs/a", b"x", "file")])


class FakeResponse:
    def __init__(self, data: bytes, length: bool = True, cut: int | None = None) -> None:
        self.data = data[:cut] if cut is not None else data
        self.headers = {"Content-Length": str(len(data))} if length else {}
        self.pos = 0

    def read(self, n: int = -1) -> bytes:
        n = len(self.data) - self.pos if n is None or n < 0 else n
        chunk = self.data[self.pos:self.pos + n]
        self.pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeNet:
    def __init__(self, routes: dict | None = None, fail_first: int = 0, always_fail: bool = False) -> None:
        self.routes, self.fail_first, self.always_fail, self.calls = routes or {}, fail_first, always_fail, 0

    def open(self, url: str, timeout: float = 60):
        self.calls += 1
        if self.always_fail or self.calls <= self.fail_first:
            raise urllib.error.URLError("down")
        return self.routes[url]


class LinkChecks(unittest.TestCase):
    def test_good_links_pass(self):
        for link in ("https://example.org/a/b.tar.gz", "https://zenodo.org/records/123/files/a.tar.gz?download=1",
                     "10.5281/zenodo.1234567"):
            self.assertIsNone(rj.check_link(link), link)

    def test_bad_links_are_refused(self):
        bad = ["http://example.org/a.tar.gz", "ftp://example.org/a", "https://example.org/a b", "https://example.org/$(id)",
               "https://example.org/a;b", "https://example.org/a&b=1", "https://example.org/`x`", "https://example.org/'x'",
               "https://127.0.0.1/a", "https://[::1]/a", "https://localhost/a", "https://user@example.org/a", "file:///etc/passwd",
               "https://example.org/" + "a" * 400, "", None, 5, "https://example.org/a\nb", "https://example.org/a\\b"]
        for link in bad:
            self.assertIsNotNone(rj.check_link(link), link)

    def test_clean_removes_what_markdown_acts_on(self):
        text = rj.clean("@evil [x](http://x) <b>y</b> `z` | # 1 $(id) a&b")
        for char in "@<>`|#$&[]":
            self.assertNotIn(char, text)
        self.assertNotIn("](", text)


class ManifestChecks(unittest.TestCase):
    programs = {"gzip", "sed"}

    def problems(self, mutate=None, stem="example", programs=None):
        doc = copy.deepcopy(EXAMPLE)
        if mutate:
            mutate(doc)
        return [p["code"] for p in rj.check_manifest(doc, stem, programs if programs is not None else self.programs)]

    def test_example_is_fine(self):
        self.assertEqual(self.problems(), [])

    def test_missing_or_wrong_form_details(self):
        self.assertEqual(self.problems(lambda d: d.pop("consent")), ["manifest_invalid"])
        self.assertEqual(self.problems(lambda d: d["consent"].update(publish_under_cc_by_4_0=False)), ["manifest_invalid"])
        self.assertEqual(self.problems(lambda d: d["bundles"][0].update(sha256="xyz")), ["manifest_invalid"])
        self.assertEqual(self.problems(lambda d: d.update(track="both")), ["manifest_invalid"])
        self.assertEqual(self.problems(lambda d: d.update(extra=1)), ["manifest_invalid"])
        self.assertEqual(self.problems(lambda d: d.update(bundles=[])), ["manifest_invalid"])

    def test_names_programs_and_repeats(self):
        self.assertIn("manifest_name", self.problems(lambda d: d.update(system_id="other")))
        self.assertIn("program_unknown", self.problems(lambda d: d["bundles"][0].update(program="nosuch")))
        self.assertIn("duplicate_run", self.problems(lambda d: d["bundles"][1].update(repeat=1)))
        self.assertIn("repeat_out_of_range", self.problems(lambda d: d["bundles"][0].update(repeat=500)))

    def test_links_are_checked_per_bundle(self):
        for link in ("https://example.org/a;rm", "https://10.0.0.1/a", "https://example.org/$(id)", "10.1234/a;b"):
            self.assertIn("link_invalid", self.problems(lambda d, link=link: d["bundles"][0].update(doi_or_url=link)), link)
        for link in ("http://example.org/a", "10.1234/with space"):   # the schema already refuses these
            self.assertNotEqual(self.problems(lambda d, link=link: d["bundles"][0].update(doi_or_url=link)), [], link)

    def test_too_many_bundles_for_one_matrix(self):
        names = [f"p{i}" for i in range(20)]

        def many(doc):
            doc["bundles"] = [{"program": n, "repeat": r, "doi_or_url": "10.0000/x", "sha256": SHA}
                              for n in names for r in range(1, 15)]
        self.assertEqual(self.problems(many, programs=set(names)), ["too_many_runs"])


class FakeApi:
    def __init__(self, files=None, content=None, fail=False, head_missing=False):
        self.files, self.content, self.fail, self.head_missing, self.calls = files, content, fail, head_missing, []

    def __call__(self, method, path, body=None, raw=False, limit=None):
        self.calls.append((method, path))
        if self.fail:
            return 500, b""
        if "/pulls/" in path:
            return 200, json.dumps(self.files).encode()
        if "/contents/" in path:
            if self.head_missing and "fork-owner" in path:
                return 404, b""
            return 200, self.content
        return 404, b""


class PrepareChecks(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_root(Path(self._tmp.name))
        self.files = [{"filename": "submissions/mytool.json", "status": "added"}]
        self.content = json.dumps(EXAMPLE).encode()

    def tearDown(self):
        self._tmp.cleanup()

    def run_prepare(self, api=None, root=None):
        api = api or FakeApi(self.files, self.content)
        return rj.prepare(api, "example-org/results", 7, HEAD, "fork-owner/results", root or self.root, sleep=NO_SLEEP)

    def codes(self, record):
        return [p["code"] for p in record["problems"]]

    def test_a_good_pull_request_gives_a_plan(self):
        record = self.run_prepare()
        self.assertTrue(record["ok"], record)
        self.assertEqual(len(record["plan"]["runs"]), 3)
        self.assertEqual(record["incomplete"], [])
        self.assertEqual(record["runs"][0]["name"], "gzip-r1")

    def test_fewer_than_three_repeats_is_noted_not_refused(self):
        doc = copy.deepcopy(EXAMPLE)
        doc["bundles"] = doc["bundles"][:2]
        record = self.run_prepare(FakeApi(self.files, json.dumps(doc).encode()))
        self.assertTrue(record["ok"])
        self.assertEqual(record["incomplete"], ["gzip"])

    def test_a_published_system_name_is_refused(self):
        root = make_root(Path(self._tmp.name) / "taken")
        (root / "systems.csv").write_text("system_id,label\nmytool,Example\n")
        record = self.run_prepare(root=root)
        self.assertEqual(self.codes(record), ["system_taken"])

    def test_missing_pins(self):
        record = self.run_prepare(root=make_root(Path(self._tmp.name) / "other", pins=False))
        self.assertEqual(self.codes(record), ["pins_missing"])

    def test_pins_that_are_not_pinned_by_digest(self):
        root = make_root(Path(self._tmp.name) / "third")
        (root / ".github" / "replay-pins.json").write_text(json.dumps({
            "runner_repository": "o/r", "runner_ref": "main", "image": "ghcr.io/o/i:latest"}))
        self.assertEqual(self.codes(self.run_prepare(root=root)), ["pins_missing"])

    def test_no_manifest_or_two(self):
        self.files = [{"filename": "README.md", "status": "modified"}]
        self.assertEqual(self.codes(self.run_prepare()), ["no_manifest"])
        self.files = [{"filename": "submissions/a.json", "status": "added"}, {"filename": "submissions/b.json", "status": "added"}]
        self.assertEqual(self.codes(self.run_prepare()), ["several_manifests"])
        self.files = [{"filename": "submissions/schema.json", "status": "modified"}]
        self.assertEqual(self.codes(self.run_prepare()), ["no_manifest"])

    def test_a_deleted_manifest_does_not_count(self):
        self.files = [{"filename": "submissions/mytool.json", "status": "removed"}]
        self.assertEqual(self.codes(self.run_prepare()), ["no_manifest"])

    def test_bad_file_names(self):
        for name in ("submissions/Example.json", "submissions/a b.json", "submissions/-x.json", "submissions/$(id).json"):
            self.files = [{"filename": name, "status": "added"}]
            self.assertEqual(self.codes(self.run_prepare()), ["manifest_name"], name)

    def test_github_down_is_reported_as_such(self):
        self.assertEqual(self.codes(self.run_prepare(FakeApi(fail=True))), ["github_unavailable"])

    def test_the_fork_can_be_gone(self):
        record = self.run_prepare(FakeApi(self.files, self.content, head_missing=True))
        self.assertTrue(record["ok"])

    def test_unreadable_json_and_huge_files(self):
        self.assertEqual(self.codes(self.run_prepare(FakeApi(self.files, b"{not json"))), ["manifest_unreadable"])
        self.assertEqual(self.codes(self.run_prepare(FakeApi(self.files, b"[" * 100000))), ["manifest_unreadable"])
        self.assertEqual(self.codes(self.run_prepare(FakeApi(self.files, b" " * (rj.MAX_MANIFEST_BYTES + 5)))), ["manifest_unreadable"])

    def test_values_in_the_plan_are_checked_again(self):
        plan = self.run_prepare()["plan"]
        run = plan["runs"][0]
        rj.check_run_entry(run)
        for key, value in (("program", "x;y"), ("repeat", 0), ("sha256", "zz"), ("source", "http://example.org/x"), ("name", "other")):
            with self.assertRaises(rj.Failure):
                rj.check_run_entry({**run, key: value})


class DownloadChecks(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.data = b"hello archive"
        self.sha = hashlib.sha256(self.data).hexdigest()
        self.url = "https://example.org/a.tar.gz"

    def tearDown(self):
        self._tmp.cleanup()

    def dl(self, net, expected=None, max_bytes=1 << 20):
        return rj.download(self.url, self.url, self.tmp / "a", expected or self.sha, max_bytes, net, NO_SLEEP)

    def code(self, net, **kw):
        with self.assertRaises(rj.Failure) as caught:
            self.dl(net, **kw)
        return caught.exception

    def test_works_after_some_failures(self):
        net = FakeNet({self.url: FakeResponse(self.data)}, fail_first=2)
        self.assertEqual(self.dl(net), len(self.data))
        self.assertEqual(net.calls, 3)

    def test_gives_up_after_a_few_tries_and_names_the_link(self):
        net = FakeNet(always_fail=True)
        exc = self.code(net)
        self.assertEqual(exc.code, "download_failed")
        self.assertEqual(net.calls, rj.DOWNLOAD_ATTEMPTS)
        self.assertIn(self.url, rj.message(exc.code, exc.params))

    def test_short_read_is_retried(self):
        net = FakeNet({self.url: FakeResponse(self.data, cut=3)})
        self.assertEqual(self.code(net).code, "download_failed")
        self.assertEqual(net.calls, rj.DOWNLOAD_ATTEMPTS)

    def test_checksum_mismatch_is_final(self):
        net = FakeNet({self.url: FakeResponse(self.data)})
        exc = self.code(net, expected="00" * 32)
        self.assertEqual(exc.code, "checksum_mismatch")
        self.assertEqual(net.calls, 1)

    def test_too_big_by_header_and_by_stream(self):
        self.assertEqual(self.code(FakeNet({self.url: FakeResponse(self.data)}), max_bytes=4).code, "too_big_download")
        self.assertEqual(self.code(FakeNet({self.url: FakeResponse(self.data, length=False)}), max_bytes=4).code, "too_big_download")

    def test_zenodo_record_with_one_file(self):
        api = "https://zenodo.org/api/records/42"
        record = {"files": [{"links": {"self": "https://zenodo.org/api/records/42/files/a.tar.gz/content"}}]}
        net = FakeNet({api: FakeResponse(json.dumps(record).encode())})
        self.assertTrue(rj.resolve_link("10.5281/zenodo.42", net, NO_SLEEP).startswith("https://zenodo.org/"))

    def test_zenodo_record_with_two_files_and_other_dois_are_unsupported(self):
        api = "https://zenodo.org/api/records/42"
        two = {"files": [{"links": {"self": "https://zenodo.org/a"}}, {"links": {"self": "https://zenodo.org/b"}}]}
        with self.assertRaises(rj.Failure) as caught:
            rj.resolve_link("10.5281/zenodo.42", FakeNet({api: FakeResponse(json.dumps(two).encode())}), NO_SLEEP)
        self.assertEqual(caught.exception.code, "link_unsupported")
        with self.assertRaises(rj.Failure) as caught:
            rj.resolve_link("10.1234/other", FakeNet(), NO_SLEEP)
        self.assertEqual(caught.exception.code, "link_unsupported")

    def test_zenodo_pointing_elsewhere_is_refused(self):
        api = "https://zenodo.org/api/records/42"
        record = {"files": [{"links": {"self": "https://evil.example.org/a"}}]}
        with self.assertRaises(rj.Failure):
            rj.resolve_link("10.5281/zenodo.42", FakeNet({api: FakeResponse(json.dumps(record).encode())}), NO_SLEEP)


class ExtractChecks(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def extract(self, data: bytes, limit: int = 1 << 20) -> Path:
        archive = self.tmp / "a.tar"
        archive.write_bytes(data)
        dest = self.tmp / "dest"
        rj.extract(archive, dest, limit, "https://example.org/a")
        return dest

    def code(self, data: bytes, limit: int = 1 << 20) -> str:
        with self.assertRaises(rj.Failure) as caught:
            self.extract(data, limit)
        return caught.exception.code

    def test_a_good_bundle_is_unpacked(self):
        dest = self.extract(good_bundle())
        self.assertTrue((dest / "tool.json").is_file() and (dest / "blobs" / "a").is_file())

    def test_unsafe_members_are_refused(self):
        self.assertEqual(self.code(tar_bytes([("../evil", b"x", "file")])), "archive_unsafe")
        self.assertEqual(self.code(tar_bytes([("/abs/evil", b"x", "file")])), "archive_unsafe")
        self.assertEqual(self.code(tar_bytes([("a/../../evil", b"x", "file")])), "archive_unsafe")
        self.assertEqual(self.code(tar_bytes([("link", None, "symlink")])), "archive_unsafe")
        self.assertFalse((self.tmp / "evil").exists())

    def test_not_an_archive(self):
        self.assertEqual(self.code(b"this is not a tar file at all" * 100), "archive_unreadable")
        self.assertEqual(self.code(good_bundle()[:40]), "archive_unreadable")

    def test_unpacked_size_is_limited(self):
        self.assertEqual(self.code(tar_bytes([("blobs/big", b"\0" * 3_000_000, "file")]), limit=1_000_000), "too_big_unpacked")


class FakeDocker:
    """Stands in for docker. ``handler(kind, args, out_dir_or_None, log)`` returns an exit code or None for a time-out."""

    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def run(self, args, timeout, log, name=None):
        if args[0] == "pull":
            kind, out = "pull", None
        else:
            command = args[args.index("agent_software_testing") + 1]
            kind = command
            out_parent = Path(next(a for a in args if a.endswith(":/results")).rsplit(":", 1)[0])
            out = out_parent / args[args.index("--out") + 1].split("/results/")[1] if "--out" in args else None
        self.calls.append(kind)
        return self.handler(kind, args, out, log)


def replay_doc(program="gzip", repeat=1, covered=100, total=3083, flake=0):
    return {"schema": "agent-software-testing/replay@0.1", "program": {"id": program}, "repeat": repeat, "branch_covered": covered,
            "branch_total": total, "cases": {"total": 5, "replayed": 5, "skipped_beyond_budget": 0},
            "status_counts": {"ok": 3, "nonzero_exit": 1, "terminated_by_signal": 1, "timeout": 0, "setup_error": 0},
            "flake": {"checked": True, "branches_differing": flake, "only_in_first": 0, "only_in_second": 0},
            "timeouts": {"clock": "wall", "count": 0, "under_load": 1, "under_load_case_ids": [SECRET]},
            "setup_error_example": SECRET}


def write_replay_out(out: Path, **kw) -> None:
    out.mkdir(parents=True)
    (out / "replay.json").write_text(json.dumps(replay_doc(**kw)))
    (out / "coverage-branches.json").write_text('{"branches": []}')
    (out / "timeline.csv").write_text("t_s,covered\n0,1\n")


def good_handler(kind, args, out, log):
    if kind == "replay":
        write_replay_out(out)
    return 0


class StageChecks(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.url = "https://example.org/gzip-r1.tar.gz"
        self.archive = good_bundle()
        self.plan = {"track": "open", "tool": {"name": "example-generator", "version": "1.0"}}
        self.run_entry = {"index": 0, "name": "gzip-r1", "program": "gzip", "repeat": 1, "source": self.url,
                          "sha256": hashlib.sha256(self.archive).hexdigest()}

    def tearDown(self):
        self._tmp.cleanup()

    def stage(self, stage="coverage", handler=good_handler, net=None, archive=None, run=None, **kw):
        if archive is not None:
            self.run_entry["sha256"] = hashlib.sha256(archive).hexdigest()
        net = net or FakeNet({self.url: FakeResponse(archive if archive is not None else self.archive)})
        docker = FakeDocker(handler)
        kw.setdefault("free_bytes", rj.RESERVE_BYTES + (64 << 20))
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = rj.run_stage(stage, run or self.run_entry, self.plan, image="example/replay:test",
                                  runner_dir=self.tmp / "runner", work=self.tmp / "work", result_dir=self.tmp / "result",
                                  data_dir=self.tmp / "data", net=net, docker=docker, sleep=NO_SLEEP, **kw)
        self.log = buf.getvalue()
        self.docker = docker
        return result

    def public_text(self) -> str:
        parts = [self.log]
        for folder in ("result", "data"):
            for path in (self.tmp / folder).rglob("*"):
                if path.is_file():
                    parts.append(path.read_text(errors="replace"))
        return "\n".join(parts)

    def test_container_flags_are_those_of_the_documentation(self):
        args = rj.container_args("img", Path("/r"), Path("/b"), Path("/o"), "n", ["validate", "/bundle"])
        for flag in ("--network", "none", "--read-only", "--pids-limit", "4096", "/tmp:exec,size=2g"):
            self.assertIn(flag, args)
        self.assertIn("/o:/results", args)
        self.assertIn("/b:/bundle:ro", args)
        self.assertNotIn("--privileged", args)

    def test_coverage_happy_path_and_nothing_private_leaks(self):
        result = self.stage()
        self.assertEqual(result["state"], "passed", result)
        self.assertEqual((result["covered"], result["total"], result["flake"]), (100, 3083, 0))
        for name in ("replay.json", "coverage-branches.json", "timeline.csv"):
            self.assertTrue((self.tmp / "data" / name).is_file(), name)
        self.assertNotIn(SECRET, self.public_text())
        self.assertEqual(self.docker.calls, ["pull", "validate", "replay"])

    def test_download_failure_names_the_link(self):
        result = self.stage(net=FakeNet(always_fail=True))
        self.assertEqual(result["code"], "download_failed")
        self.assertIn(self.url, rj.message(result["code"], result["params"]))

    def test_checksum_mismatch(self):
        self.run_entry["sha256"] = "11" * 32
        result = self.stage(net=FakeNet({self.url: FakeResponse(self.archive)}))
        self.assertEqual(result["code"], "checksum_mismatch")
        text = rj.message(result["code"], result["params"])
        self.assertNotIn("....", text)
        self.assertEqual(text.count(self.run_entry["sha256"]), 1)

    def test_archive_problems(self):
        self.assertEqual(self.stage(archive=b"junk" * 50)["code"], "archive_unreadable")
        self.tearDown(); self.setUp()
        self.assertEqual(self.stage(archive=tar_bytes([("../x", b"1", "file")]))["code"], "archive_unsafe")

    def test_bundle_in_a_subfolder(self):
        nested = tar_bytes([("inner/tool.json", b"{}", "file"), ("inner/cases.jsonl", b"", "file")])
        self.assertEqual(self.stage(archive=nested)["code"], "bundle_layout")

    def test_bundle_that_disagrees_with_the_manifest(self):
        result = self.stage(archive=good_bundle(repeat=2))
        self.assertEqual(result["code"], "bundle_mismatch")
        self.assertIn("repeat", result["params"]["fields"])
        self.tearDown(); self.setUp()
        self.assertEqual(self.stage(archive=good_bundle(program="sed"))["code"], "bundle_mismatch")

    def test_bundle_too_big_for_the_disk(self):
        result = self.stage(free_bytes=rj.RESERVE_BYTES + 10)
        self.assertEqual(result["code"], "too_big_download")
        self.tearDown(); self.setUp()
        big = tar_bytes([("tool.json", json.dumps(tool_json()).encode(), "file"), ("cases.jsonl", b"", "file"),
                         ("blobs/zero", b"\0" * 5_000_000, "file")])
        result = self.stage(archive=big, free_bytes=rj.RESERVE_BYTES + 4_000_000)
        self.assertEqual(result["code"], "too_big_unpacked")

    def test_invalid_bundle_reports_the_validator_errors(self):
        def handler(kind, args, out, log):
            if kind == "validate":
                Path(log).write_text("error: cases.jsonl line 2: bad hash\nwarning: ignored\nerror: second problem\n")
                return 1
            return good_handler(kind, args, out, log)
        result = self.stage(handler=handler)
        self.assertEqual(result["code"], "bundle_invalid")
        self.assertEqual(len(result["params"]["errors"]), 2)
        self.assertNotIn("replay", self.docker.calls)

    def test_a_crashing_test_case_is_normal(self):
        # the program under test crashed on cases: the runner still exits 0 and the run passes
        self.assertEqual(self.stage()["state"], "passed")

    def test_runner_error_is_retried_once_then_reported(self):
        counts = {"n": 0}

        def handler(kind, args, out, log):
            if kind == "replay":
                counts["n"] += 1
                return 1
            return good_handler(kind, args, out, log)
        result = self.stage(handler=handler)
        self.assertEqual((result["code"], result["attempts"], counts["n"]), ("replay_failed", rj.REPLAY_ATTEMPTS, rj.REPLAY_ATTEMPTS))

    def test_runner_error_that_goes_away_on_retry(self):
        counts = {"n": 0}

        def handler(kind, args, out, log):
            if kind == "replay":
                counts["n"] += 1
                if counts["n"] == 1:
                    return 125
            return good_handler(kind, args, out, log)
        result = self.stage(handler=handler)
        self.assertEqual((result["state"], result["attempts"]), ("passed", 2))

    def test_time_limit(self):
        def handler(kind, args, out, log):
            return None if kind == "replay" else good_handler(kind, args, out, log)
        result = self.stage(handler=handler)
        self.assertEqual(result["code"], "job_limit")
        self.assertEqual(self.docker.calls.count("replay"), 1)

    def test_image_that_cannot_be_pulled(self):
        result = self.stage(handler=lambda *a: 1)
        self.assertEqual(result["code"], "image_unavailable")

    def test_outputs_that_do_not_belong_to_the_run_are_refused(self):
        def handler(kind, args, out, log):
            if kind == "replay":
                write_replay_out(out, program="sed")
                return 0
            return good_handler(kind, args, out, log)
        self.assertEqual(self.stage(handler=handler)["code"], "replay_failed")

    def test_a_result_file_is_always_written(self):
        self.stage(net=FakeNet(always_fail=True))
        self.assertTrue((self.tmp / "result" / "result.json").is_file())

    def test_program_output_never_reaches_the_log(self):
        def handler(kind, args, out, log):
            Path(log).write_text(f"ERROR: AddressSanitizer: heap-buffer-overflow in {SECRET}\n")
            return good_handler(kind, args, out, log)
        self.stage(handler=handler)
        self.assertNotIn(SECRET, self.public_text())

    def test_finish_records_a_missing_result(self):
        os.environ.update(RESULT_DIR=str(self.tmp / "r2"), STAGE="coverage", RUN_NAME="gzip-r1", JOB_STATUS="cancelled")
        try:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(rj.cmd_finish(), 0)
            self.assertEqual(json.loads((self.tmp / "r2" / "result.json").read_text())["code"], "cancelled")
        finally:
            for key in ("RESULT_DIR", "STAGE", "RUN_NAME", "JOB_STATUS"):
                os.environ.pop(key, None)


class CommentChecks(unittest.TestCase):
    runs = [{"index": 0, "name": "gzip-r1", "program": "gzip", "repeat": 1},
            {"index": 1, "name": "gzip-r2", "program": "gzip", "repeat": 2}]
    record = {"ok": True, "problems": [], "system_id": "example", "runs": runs, "programs": ["gzip"], "incomplete": ["gzip"]}

    def passed(self, **kw):
        return {"state": "passed", "code": None, "covered": 5, "total": 10, "flake": 0, **kw}

    def failed(self, code, **params):
        return {"state": "failed", "code": code, "params": params}

    def sig_ok(self, rows=None):
        return {"state": "passed", "code": None, "repeats": [], "rows": rows or [{"sanitizer": "ASan", "kind": "none", "signatures": 0}]}

    def test_all_good(self):
        results = {("coverage", r["name"]): self.passed() for r in self.runs}
        results[("signatures", "gzip")] = self.sig_ok()
        text, ok = rj.build_comment(self.record, results, {"approve": "success"}, "https://example.org/run")
        self.assertTrue(ok)
        self.assertTrue(text.startswith(rj.MARKER))
        self.assertEqual(text.count("| Program |"), 1)

    def test_some_pass_and_some_fail_means_failed_and_sanitiser_not_started(self):
        results = {("coverage", "gzip-r1"): self.passed(), ("coverage", "gzip-r2"): self.failed("download_failed", link="https://example.org/x")}
        text, ok = rj.build_comment(self.record, results, {"approve": "success"}, "https://example.org/run")
        self.assertFalse(ok)
        self.assertIn("https://example.org/x", text)
        self.assertIn("5 / 10", text)
        self.assertIn("not started", text)

    def test_sanitiser_kinds_and_counts_are_shown_per_program(self):
        results = {("coverage", r["name"]): self.passed() for r in self.runs}
        results[("signatures", "gzip")] = self.sig_ok([{"sanitizer": "ASan", "kind": "SEGV", "signatures": 2}])
        text, ok = rj.build_comment(self.record, results, {"approve": "success"}, "u")
        self.assertTrue(ok)
        self.assertIn("ASan SEGV 2", text)

    def test_a_failed_repeat_is_named(self):
        results = {("coverage", r["name"]): self.passed() for r in self.runs}
        results[("signatures", "gzip")] = {"state": "failed", "code": "download_failed", "params": {}, "repeats": [
            {"repeat": 1, "state": "passed", "code": None, "params": {}},
            {"repeat": 2, "state": "failed", "code": "download_failed", "params": {"link": "https://example.org/y"}}]}
        text, ok = rj.build_comment(self.record, results, {"approve": "success"}, "u")
        self.assertFalse(ok)
        self.assertIn("https://example.org/y", text)
        self.assertIn("1 of 2", text)

    def test_not_approved(self):
        text, ok = rj.build_comment(self.record, {}, {"approve": "failure"}, "u")
        self.assertFalse(ok)

    def test_manifest_problems_are_listed(self):
        record = {"ok": False, "problems": [{"code": "manifest_invalid", "errors": ["$.consent: is missing"]}], "runs": []}
        text, ok = rj.build_comment(record, {}, {}, "u")
        self.assertFalse(ok)
        self.assertIn("consent", text)

    def test_manifest_problems_name_the_field_as_written(self):
        document = json.loads((rj.ROOT / "submissions" / "example.json").read_text())
        document["bundles"][1]["doi_or_url"] = "http://example.org/x.tar.gz"
        document["consent"]["publish_under_cc_by_4_0"] = False
        problems = rj.check_manifest(document, "example", {"gzip"})
        text, _ok = rj.build_comment({"ok": False, "problems": problems, "runs": []}, {}, {}, "u")
        self.assertIn("`bundles.1.doi_or_url`: is not in the form", text)
        self.assertIn("`consent.publish_under_cc_by_4_0`", text)

    def test_manifest_problem_text_cannot_write_markdown(self):
        for evil in ("$.a[0]`x: @maintainer [click](http://evil.example) <b>", "$: `oops` [x](y)"):
            line = rj.manifest_error(evil)
            self.assertNotIn("@maintainer", line.replace("`", ""))
            for bad in ("[", "]", "<", ">"):
                self.assertNotIn(bad, line)
            self.assertEqual(line.count("`") % 2, 0)

    def test_flake_count_reads_as_a_sentence(self):
        for flake, words in ((0, "0 branches differ"), (1, "1 branch differs"), (3, "3 branches differ")):
            self.assertEqual(rj.flake_words(flake), words)

    def test_a_checksum_mismatch_shows_both_hashes_whole(self):
        got, expected = "a" * 63 + "0", "a" * 63 + "1"
        line = rj.MESSAGES["checksum_mismatch"]({"link": "https://example.org/x.tar.gz", "got": got, "expected": expected})
        self.assertIn(f"`{got}`", line)
        self.assertIn(f"`{expected}`", line)

    def test_artifact_content_is_untrusted(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "result-coverage-gzip-r1"
            folder.mkdir()
            evil = {"stage": "coverage", "name": "gzip-r1", "state": "failed", "code": "bundle_invalid",
                    "params": {"errors": ["@maintainer [click](http://evil.example) <script>x</script> `rm`"]},
                    "message": "@everyone", "covered": "<img>", "kinds": {"asan": {"x y": 5}}}
            (folder / "result.json").write_text(json.dumps(evil))
            loaded = rj.load_result(folder / "result.json", "coverage", "gzip-r1")
            text, _ = rj.build_comment(self.record, {("coverage", "gzip-r1"): loaded}, {"approve": "success"}, "u")
        for bad in ("@maintainer", "@everyone", "](http", "<script", "<img", "`rm`"):
            self.assertNotIn(bad, text)

    def test_missing_or_wrong_result_files_count_as_lost(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(rj.load_result(Path(tmp) / "none.json", "coverage", "gzip-r1")["code"], "lost")
            wrong = Path(tmp) / "r.json"
            wrong.write_text(json.dumps({"stage": "coverage", "name": "other", "state": "passed"}))
            self.assertEqual(rj.load_result(wrong, "coverage", "gzip-r1")["state"], "lost")
            wrong.write_text("[1, 2")
            self.assertEqual(rj.load_result(wrong, "coverage", "gzip-r1")["state"], "lost")

    def test_every_code_has_a_message_for_empty_parameters(self):
        for code in rj.MESSAGES:
            self.assertTrue(rj.message(code, {}))

    def test_comment_is_updated_in_place_and_not_taken_from_a_user(self):
        class Api:
            def __init__(self, comments):
                self.comments, self.sent = comments, []

            def __call__(self, method, path, body=None, raw=False, limit=None):
                if method == "GET":
                    return 200, json.dumps(self.comments).encode()
                self.sent.append((method, path))
                return 201, b"{}"
        mine = {"id": 5, "body": rj.MARKER + "\nold", "user": {"type": "Bot"}}
        spoof = {"id": 6, "body": rj.MARKER + "\nfake", "user": {"type": "User"}}
        api = Api([spoof, mine])
        self.assertEqual(rj.post_comment(api, "o/r", 3, rj.MARKER + "new"), "updated")
        self.assertEqual(api.sent, [("PATCH", "/repos/o/r/issues/comments/5")])
        api = Api([spoof])
        self.assertEqual(rj.post_comment(api, "o/r", 3, rj.MARKER + "new"), "created")
        self.assertEqual(api.sent[0][0], "POST")




DETAIL = "SECRET-DETAIL-qrs"
CASE = "SECRET-CASE-0123"
KINDS = {"asan": ("heap-buffer-overflow", "double-free"), "ubsan": ("shift-exponent",)}


def sig_handler(found: dict, extra=None):
    """A fake signature pass. ``found`` maps a repeat to a list of (sanitiser, kind, frame)."""
    def handler(kind, args, out, log):
        if kind != "signatures":
            return 0
        repeat = int(out.name.split("-r")[1].split("-a")[0])
        sigs = found.get(repeat, [])
        by_kind: dict = {}
        for san, k, frame in sigs:
            by_kind.setdefault(san, {}).setdefault(k, 0)
            by_kind[san][k] += 1
        out.mkdir(parents=True)
        (out / "signatures-counts.json").write_text(json.dumps({
            "schema": "agent-software-testing/signature-counts@0.1", "program": "gzip", "repeat": repeat, "signatures": len(sigs),
            "by_sanitizer_and_kind": by_kind, "cases": {"replayed": 5}, "status_counts": {"ok": 5}, "tool": {"name": DETAIL}}))
        (out / "signatures-private.json").write_text(json.dumps({"signatures": [
            {"sanitizer": san, "kind": k, "frame": frame, "detail": DETAIL, "examples": [CASE]} for san, k, frame in sigs]}))
        if extra:
            extra(out)
        return 0
    return handler


class SignatureChecks(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.plan = {"system_id": "mytool", "track": "open", "tool": {"name": "example-generator", "version": "1.0"}}
        self.archives, self.runs, self.routes = {}, [], {}
        for repeat in (1, 2, 3):
            data = good_bundle(repeat=repeat)
            url = f"https://example.org/gzip-r{repeat}.tar.gz"
            self.routes[url] = data
            self.runs.append({"index": repeat - 1, "name": f"gzip-r{repeat}", "program": "gzip", "repeat": repeat,
                              "source": url, "sha256": hashlib.sha256(data).hexdigest()})

    def tearDown(self):
        self._tmp.cleanup()

    def net(self):
        class Net(FakeNet):
            def open(inner, url, timeout=60):
                inner.calls += 1
                if self.fail_url == url:
                    raise urllib.error.URLError("down")
                return FakeResponse(self.routes[url])
        return Net()

    fail_url = None

    def go(self, handler, budget_s=1e9, clock=None, **kw):
        docker = FakeDocker(handler)
        buf = io.StringIO()
        kw.setdefault("free_bytes", rj.RESERVE_BYTES + (64 << 20))
        with redirect_stdout(buf):
            result = rj.run_signatures_program("gzip", self.runs, self.plan, image="example/replay:test",
                                               runner_dir=self.tmp / "runner", work=self.tmp / "work",
                                               result_dir=self.tmp / "result", data_dir=self.tmp / "data", net=self.net(),
                                               docker=docker, sleep=NO_SLEEP, budget_s=budget_s, clock=clock or __import__("time").monotonic, **kw)
        self.log = buf.getvalue()
        self.docker = docker
        return result

    def uploaded_text(self) -> str:
        parts = [self.log]
        for folder in ("result", "data"):
            for path in (self.tmp / folder).rglob("*"):
                if path.is_file():
                    parts.append(path.read_text(errors="replace"))
        return "\n".join(parts)

    def rows(self):
        with open(self.tmp / "data" / "signature-rows.csv", newline="") as handle:
            return list(__import__("csv").reader(handle))

    def test_a_signature_found_in_two_repeats_counts_once(self):
        found = {1: [("asan", "double-free", "a.c:f")], 2: [("asan", "double-free", "a.c:f"), ("asan", "heap-buffer-overflow", "b.c:g")],
                 3: [("ubsan", "shift-exponent", "c.c:h")]}
        result = self.go(sig_handler(found))
        self.assertEqual(result["state"], "passed", result)
        rows = {(r[2], r[3]): (r[4], r[5]) for r in self.rows()[1:]}
        self.assertEqual(rows[("ASan", "double-free")], ("1", "3"))
        self.assertEqual(rows[("ASan", "heap-buffer-overflow")], ("1", "3"))
        self.assertEqual(rows[("UBSan", "shift exponent too large")], ("1", "3"))
        self.assertEqual(self.rows()[0], rj.SIGNATURE_COLUMNS)

    def test_nothing_found_gives_none_rows(self):
        result = self.go(sig_handler({}))
        self.assertEqual(result["state"], "passed")
        self.assertEqual({(r[2], r[3], r[4]) for r in self.rows()[1:]}, {("ASan", "none", "0"), ("UBSan", "none", "0")})

    def test_repeats_run_in_turn_and_only_one_bundle_is_kept(self):
        seen = []

        def handler(kind, args, out, log):
            if kind == "signatures":
                seen.append(sorted(p.name for p in (self.tmp / "work").iterdir() if p.name.startswith(("bundle", "archive"))))
            return sig_handler({})(kind, args, out, log)
        self.go(handler)
        self.assertEqual(seen, [["bundle"]] * 3)
        self.assertFalse((self.tmp / "work" / "bundle").exists())
        self.assertFalse((self.tmp / "work" / "archive.tar").exists())

    def test_no_frame_detail_or_case_id_reaches_an_upload_or_the_log(self):
        found = {1: [("asan", "double-free", "SECRET-FRAME-xyz")], 2: [("asan", "double-free", "SECRET-FRAME-xyz")]}

        def handler(kind, args, out, log):
            Path(log).write_text(f"ERROR: AddressSanitizer: double-free {DETAIL} {CASE} SECRET-FRAME-xyz\n")
            return sig_handler(found)(kind, args, out, log)
        self.go(handler)
        text = self.uploaded_text()
        for secret in (SECRET, DETAIL, CASE, "SECRET-FRAME-xyz"):
            self.assertNotIn(secret, text)
        names = sorted(p.name for p in (self.tmp / "data").iterdir())
        self.assertEqual(names, ["signature-rows.csv", "signatures-counts-r1.json", "signatures-counts-r2.json", "signatures-counts-r3.json"])
        self.assertFalse(list((self.tmp / "work").rglob("signatures-private.json")))

    def test_a_kind_that_is_not_listed_is_reported_as_the_maintainers_task(self):
        result = self.go(sig_handler({1: [("asan", "brand-new-kind", "a.c:f")]}))
        self.assertEqual((result["state"], result["code"]), ("failed", "signature_kind_unknown"))
        self.assertFalse((self.tmp / "data" / "signature-rows.csv").exists())
        self.assertNotIn("a.c:f", self.uploaded_text())   # kinds are public, frames are not

    def test_one_failed_repeat_fails_the_program_and_the_others_are_kept(self):
        self.fail_url = self.runs[1]["source"]
        result = self.go(sig_handler({}))
        self.assertEqual((result["state"], result["code"]), ("failed", "download_failed"))
        states = {e["repeat"]: e["state"] for e in result["repeats"]}
        self.assertEqual(states, {1: "passed", 2: "failed", 3: "passed"})
        self.assertTrue((self.tmp / "data" / "signatures-counts-r1.json").is_file())
        self.assertFalse((self.tmp / "data" / "signature-rows.csv").exists())

    def test_retry_is_per_repeat(self):
        tries = {"n": 0}

        def handler(kind, args, out, log):
            if kind == "signatures" and "-r2-" in out.name:
                tries["n"] += 1
                if tries["n"] == 1:
                    return 1
            return sig_handler({})(kind, args, out, log)
        result = self.go(handler)
        self.assertEqual(result["state"], "passed")
        self.assertEqual([e["attempts"] for e in result["repeats"]], [1, 2, 1])

    def test_the_time_budget_is_shared_by_the_repeats(self):
        ticks = iter(range(0, 10000, 100))
        clock = lambda: next(ticks)  # noqa: E731

        def handler(kind, args, out, log):
            if kind == "signatures" and "-r2-" in out.name:
                return None
            return sig_handler({})(kind, args, out, log)
        result = self.go(handler, clock=clock)
        self.assertEqual(result["code"], "job_limit")
        self.assertEqual(result["repeats"][2]["code"], "job_limit")

    def test_counts_that_belong_to_another_program_are_refused(self):
        def handler(kind, args, out, log):
            code = sig_handler({})(kind, args, out, log)
            if kind == "signatures":
                doc = json.loads((out / "signatures-counts.json").read_text())
                doc["program"] = "sed"
                (out / "signatures-counts.json").write_text(json.dumps(doc))
            return code
        self.assertEqual(self.go(handler)["code"], "signatures_failed")


class SymlinkChecks(unittest.TestCase):
    """The container can write the output mount. A planted link must never lead the host to a file outside it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.host = self.tmp / "host-file.txt"
        self.host.write_text("HOSTSECRET")
        self.hostdir = self.tmp / "hostdir"
        self.hostdir.mkdir()
        self.url = "https://example.org/gzip-r1.tar.gz"
        self.archive = good_bundle()
        self.run_entry = {"index": 0, "name": "gzip-r1", "program": "gzip", "repeat": 1, "source": self.url,
                          "sha256": hashlib.sha256(self.archive).hexdigest()}
        self.plan = {"system_id": "mytool", "track": "open", "tool": {"name": "example-generator", "version": "1.0"}}

    def tearDown(self):
        self._tmp.cleanup()

    def uploaded(self) -> str:
        parts = []
        for folder in ("result", "data"):
            for path in (self.tmp / folder).rglob("*"):
                if path.is_file():
                    parts.append(path.read_text(errors="replace"))
        return "\n".join(parts)

    def coverage(self, plant):
        def handler(kind, args, out, log):
            if kind == "replay":
                write_replay_out(out)
                plant(out)
            return 0
        with redirect_stdout(io.StringIO()):
            return rj.run_stage("coverage", self.run_entry, self.plan, image="example/replay:test", runner_dir=self.tmp / "r",
                                work=self.tmp / "work", result_dir=self.tmp / "result", data_dir=self.tmp / "data",
                                net=FakeNet({self.url: FakeResponse(self.archive)}), docker=FakeDocker(handler), sleep=NO_SLEEP,
                                free_bytes=rj.RESERVE_BYTES + (64 << 20))

    def signatures(self, plant):
        def handler(kind, args, out, log):
            code = sig_handler({1: [("asan", "double-free", "a.c:f")]})(kind, args, out, log)
            if kind == "signatures":
                plant(out)
            return code
        with redirect_stdout(io.StringIO()):
            return rj.run_signatures_program("gzip", [self.run_entry], self.plan, image="example/replay:test",
                                             runner_dir=self.tmp / "r", work=self.tmp / "work", result_dir=self.tmp / "result",
                                             data_dir=self.tmp / "data", net=FakeNet({self.url: FakeResponse(self.archive)}),
                                             docker=FakeDocker(handler), sleep=NO_SLEEP, free_bytes=rj.RESERVE_BYTES + (64 << 20))

    def replace_with_link(self, path: Path, target: Path):
        path.unlink()
        path.symlink_to(target)

    def test_coverage_files_that_are_links(self):
        for name in ("replay.json", "coverage-branches.json", "timeline.csv"):
            with self.subTest(name=name):
                self.setUp()
                result = self.coverage(lambda out, name=name: self.replace_with_link(out / name, self.host))
                self.assertEqual((result["state"], result["code"]), ("failed", "replay_failed"))
                self.assertNotIn("HOSTSECRET", self.uploaded())
                self.tearDown()

    def test_coverage_output_folder_that_is_a_link(self):
        def plant(out):
            real = out.with_name(out.name + "-real")
            out.rename(real)
            out.symlink_to(real)
        result = self.coverage(plant)
        self.assertEqual(result["code"], "replay_failed")
        self.assertNotIn("HOSTSECRET", self.uploaded())

    def test_coverage_link_to_a_folder_outside(self):
        (self.hostdir / "replay.json").write_text("HOSTSECRET")

        def plant(out):
            for name in ("replay.json", "coverage-branches.json", "timeline.csv"):
                (out / name).unlink()
            for name in ("replay.json",):
                (out / name).symlink_to(self.hostdir / name)
        self.assertEqual(self.coverage(plant)["code"], "replay_failed")
        self.assertNotIn("HOSTSECRET", self.uploaded())

    def test_signature_files_and_entries_that_are_links(self):
        for name in ("signatures-counts.json", "signatures-private.json"):
            with self.subTest(name=name):
                self.setUp()
                result = self.signatures(lambda out, name=name: self.replace_with_link(out / name, self.host))
                self.assertEqual(result["state"], "failed")
                self.assertEqual(result["repeats"][0]["code"], "signatures_failed")
                self.assertNotIn("HOSTSECRET", self.uploaded())
                self.tearDown()
        self.setUp()
        result = self.signatures(lambda out: (out / "extra").symlink_to(self.host))
        self.assertEqual(result["repeats"][0]["code"], "signatures_failed")
        self.tearDown()
        self.setUp()
        result = self.signatures(lambda out: (out / "private").symlink_to(self.hostdir))
        self.assertEqual(result["repeats"][0]["code"], "signatures_failed")
        self.assertNotIn("HOSTSECRET", self.uploaded())

    def test_a_link_for_the_private_file_does_not_survive_to_rows_for(self):
        # the check happens again just before rows_for reads the folders
        def plant(out):
            self.replace_with_link(out / "signatures-private.json", self.host)
        result = self.signatures(plant)
        self.assertEqual(result["state"], "failed")
        self.assertFalse((self.tmp / "data" / "signature-rows.csv").exists())

    def test_the_helpers_themselves(self):
        link = self.tmp / "link"
        link.symlink_to(self.host)
        with self.assertRaises(OSError):
            rj.secure_open(link)
        with self.assertRaises(OSError):
            rj.read_output_json(link)
        folder = self.tmp / "folder"
        folder.symlink_to(self.hostdir)
        with self.assertRaises(OSError):
            rj.check_output_folder(folder)
        self.assertFalse(rj.is_real_dir(folder))


class ReviewFindings(unittest.TestCase):
    def test_a_link_that_is_not_https_says_so(self):
        self.assertEqual(rj.check_link("http://example.org/a.tar.gz"), "not https and not a DOI")

    def test_an_address_written_another_way_is_still_an_address(self):
        for link in ("https://2852039166/x", "https://0xa9fea9fe/x", "https://127.1/x", "https://localhost./x"):
            self.assertIsNotNone(rj.check_link(link), link)
        self.assertIsNone(rj.check_link("https://zenodo.org/records/1/files/a.tar.gz"))

    def test_a_host_name_must_not_lead_to_the_runners_own_network(self):
        def resolving_to(*addresses):
            return lambda host, port, **kw: [(2, 1, 6, "", (a, port)) for a in addresses]

        with mock.patch.object(rj.socket, "getaddrinfo", resolving_to("93.184.216.34")):
            self.assertTrue(rj.reaches_only_public_addresses("example.org"))
        for address in ("169.254.169.254", "10.0.0.1", "127.0.0.1", "::1"):
            with mock.patch.object(rj.socket, "getaddrinfo", resolving_to("93.184.216.34", address)):
                self.assertFalse(rj.reaches_only_public_addresses("example.org"), address)
        with mock.patch.object(rj.socket, "getaddrinfo", side_effect=OSError):
            self.assertFalse(rj.reaches_only_public_addresses("example.org"))
        with mock.patch.object(rj.socket, "getaddrinfo", resolving_to("10.0.0.1")):
            with self.assertRaises(urllib.error.URLError):
                rj.Net().open("https://example.org/x")

    def test_a_huge_manifest_error_stays_small_enough_to_be_reported(self):
        example = json.loads((ROOT / "submissions" / "example.json").read_text())
        example["\u4e2d" * 340000] = 1
        problems = rj.check_manifest(example, "example", {example["bundles"][0]["program"]})
        self.assertEqual(problems[0]["code"], "manifest_invalid")
        self.assertLess(len(json.dumps(problems)), 20000)

    def test_the_comment_names_its_commit_and_how_many_cases_were_measured(self):
        record = {"ok": True, "system_id": "mytool", "problems": [], "programs": ["gzip"],
                  "runs": [{"index": 0, "name": "gzip-r1", "program": "gzip", "repeat": 1}]}
        results = {("coverage", "gzip-r1"): {"state": rj.STATE_PASSED, "code": None, "covered": 5, "total": 9, "cases": 7,
                                             "timeouts": 2, "not_started": 1, "flake": 0, "params": {}},
                   ("signatures", "gzip"): {"state": rj.STATE_PASSED, "code": None, "rows": [], "repeats": [], "params": {}}}
        needs = {"prepare": "success", "approve": "success", "coverage": "success", "sanitiser": "success"}
        body, ok = rj.build_comment(record, results, needs, "https://example.org/run", "a" * 40)
        self.assertTrue(ok)
        self.assertIn("For commit `aaaaaaaaaaaa`.", body)
        self.assertIn("7 (2 timed out, 1 did not start)", body)
        body, _ok = rj.build_comment(record, results, needs, "https://example.org/run", "`injected`")
        self.assertNotIn("injected", body)

    def test_a_refused_comment_fails_the_job(self):
        def api(method, path, body=None, **kw):
            return (200, b"[]") if method == "GET" else (403, b"")

        with self.assertRaises(OSError):
            rj.post_comment(api, "o/r", 1, "text")

    def test_the_container_has_a_memory_bound(self):
        args = rj.container_args("image", Path("/r"), Path("/b"), Path("/o"), "name", ["validate"])
        self.assertEqual(args[args.index("--memory") + 1], "14g")


@unittest.skipUnless(os.environ.get("REPLAY_REAL_OUTPUT"), "REPLAY_REAL_OUTPUT is not set")
class RealOutputs(unittest.TestCase):
    def test_real_runner_output_goes_through_the_happy_path(self):
        import shutil
        real = Path(os.environ["REPLAY_REAL_OUTPUT"])
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            url = "https://example.org/gzip-r1.tar.gz"
            archive = good_bundle()

            def handler(kind, args, out, log):
                if kind == "replay":
                    shutil.copytree(real / "gzip-r1", out)
                elif kind == "signatures":
                    shutil.copytree(real / "gzip-r1-signatures", out)
                return 0
            run = {"index": 0, "name": "gzip-r1", "program": "gzip", "repeat": 1, "source": url,
                   "sha256": hashlib.sha256(archive).hexdigest()}
            plan = {"system_id": "mytool", "track": "open", "tool": {"name": "example-generator", "version": "1.0"}}
            class FreshNet(FakeNet):
                def open(self, u, timeout=60):
                    return FakeResponse(archive)
            common = dict(image="example/replay:test", runner_dir=tmp / "r", net=FreshNet(),
                          docker=FakeDocker(handler), sleep=NO_SLEEP, free_bytes=rj.RESERVE_BYTES + (64 << 20))
            with redirect_stdout(io.StringIO()):
                cov = rj.run_stage("coverage", run, plan, work=tmp / "w1", result_dir=tmp / "res1", data_dir=tmp / "d1", **common)
                sig = rj.run_signatures_program("gzip", [run], plan, work=tmp / "w2", result_dir=tmp / "res2", data_dir=tmp / "d2", **common)
            self.assertEqual(cov["state"], "passed", cov)
            self.assertEqual(cov["total"], 3083)
            self.assertEqual(sig["state"], "passed", sig)


if __name__ == "__main__":
    unittest.main()
