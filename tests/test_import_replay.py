# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Agent Software Testing authors
"""Promises I1 to I4 (tests/README.md): new replays enter the release through scripts/import_replay.py, which takes
only runs that were replayed, checks the replay's tables against each other and against the release before it changes
a file, and writes what it read.

The replay tables are made from runs of the shipped release, so the import has to give those runs back.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import import_replay  # noqa: E402

CELLS = [("klee-fixed-6w", "bc"), ("klee-fixed-6w", "gzip"), ("aflpp", "confetti")]
NOT_REPLAYED = ("klee-fixed-6w", "confetti", 1)


def read_csv(path: Path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def run_script(root: Path, script: str, *args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(root / "scripts" / script), *map(str, args)], capture_output=True, text=True, timeout=300)


def digest_tree(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes())
    return h.hexdigest()


def make_tables(directory: Path) -> Path:
    """What a standard replay writes, for the runs of CELLS (repeats 1 to 3) and one run that was not replayed."""

    runs = [r for r in read_csv(ROOT / "runs.csv") if (r["system_id"], r["program"]) in CELLS and int(r["repeat"]) <= 3]
    state = [[r["system_id"], r["program"], r["repeat"], f"{r['system_id']}/{r['program']}/{r['repeat']}", "done"] for r in runs]
    state.append([*NOT_REPLAYED[:2], NOT_REPLAYED[2], "klee-fixed-6w/confetti/1", "missing"])
    write_csv(directory / "runs.csv", ["system_id", "program", "repeat", "cell", "state"], state)
    write_csv(directory / "coverage.csv", ["system_id", "program", "repeat", "covered", "total"],
              [[r["system_id"], r["program"], r["repeat"], r["covered"], r["total"]] for r in runs])
    for r in runs:
        for name in (r["set_path"], r["curve_path"]):
            (directory / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, directory / name)
    write_csv(directory / "signature-runs.csv", ["system_id", "program", "repeat", "sanitizer"], [])
    write_csv(directory / "signatures-by-system.csv", ["system_id", "program", "sanitizer", "kind", "signatures", "runs"], [])
    (directory / "replay-provenance.json").write_text(json.dumps({
        "done": len(runs), "runs": len(state),
        "values": {"profile": {"standard": 1}, "runner_version": {"0.1.4": 1}, "runner_manifest_sha256": {"a" * 64: 1},
                   "image": {"/some/cluster/path/runner.tar sha256=" + "b" * 64 + " record sha256=" + "c" * 64: 1}},
    }))
    manifest = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(directory).as_posix()}\n"
                for p in sorted(directory.rglob("*")) if p.is_file()]
    (directory / "MANIFEST.sha256").write_text("".join(manifest))
    return directory


def copy_release(destination: Path) -> Path:
    shutil.copytree(ROOT, destination, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "tests"))
    return destination


@pytest.fixture(scope="module")
def untouched_repo(tmp_path_factory):
    """A copy of the release that the refusing tests share: each checks that it is still byte for byte as it was."""

    return copy_release(tmp_path_factory.mktemp("repo") / "repo")


@pytest.fixture
def world(tmp_path, untouched_repo):
    return {"tables": make_tables(tmp_path / "tables"), "repo": untouched_repo}


def refreshed_manifest(tables: Path) -> None:
    lines = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(tables).as_posix()}\n"
             for p in sorted(tables.rglob("*")) if p.is_file() and p.name != "MANIFEST.sha256"]
    (tables / "MANIFEST.sha256").write_text("".join(lines))


def test_an_import_gives_back_exactly_the_runs_it_was_given_and_leaves_a_release_that_passes_its_check(world):
    """I1. Only done runs enter; their numbers, sets and curves are those of the tables; the systems and tools without
    a run leave; the image is recorded by its digest and not by the path of the cluster it was run on; the result
    passes check.py once the derived tables and the README table are refreshed."""

    tables = world["tables"]
    repo = copy_release(tables.parent / "repo")
    done = run_script(repo, "import_replay.py", tables, "--clean")
    assert done.returncode == 0, done.stdout + done.stderr

    expected = [r for r in read_csv(ROOT / "runs.csv") if (r["system_id"], r["program"]) in CELLS and int(r["repeat"]) <= 3]
    got = read_csv(repo / "runs.csv")
    assert [(r["system_id"], r["program"], r["repeat"], r["covered"], r["total"]) for r in got] == \
           [(r["system_id"], r["program"], r["repeat"], r["covered"], r["total"]) for r in expected]
    assert (NOT_REPLAYED[0], NOT_REPLAYED[1], "1") not in {(r["system_id"], r["program"], r["repeat"]) for r in got}
    for r in got:
        original = json.loads(gzip.decompress((ROOT / r["set_path"]).read_bytes()))
        written = json.loads(gzip.decompress((repo / r["set_path"]).read_bytes()))
        assert written["covered_branch_keys"] == original["covered_branch_keys"] and written["branch_covered"] == int(r["covered"])
        assert (repo / r["curve_path"]).read_text() == (ROOT / r["curve_path"]).read_text()
    assert sorted(p.name for p in (repo / "sets").iterdir()) == ["aflpp", "klee-fixed-6w"]
    assert {s["system_id"] for s in read_csv(repo / "systems.csv")} == {"aflpp", "klee-fixed-6w"}
    assert {t["tool_id"] for t in read_csv(repo / "tools.csv")} == {"aflpp", "klee"}

    record = json.loads((repo / "RELEASE.json").read_text())
    assert record["image"] == "b" * 64 and "cluster" not in json.dumps(record)
    assert record["counts"]["runs"] == len(expected)

    assert run_script(repo, "derive.py").returncode == 0
    final = run_script(repo, "check.py")
    assert final.returncode == 0, final.stdout + final.stderr


def _wrong_total(tables):
    rows = read_csv(tables / "coverage.csv")
    rows[0]["total"] = str(int(rows[0]["total"]) + 1)
    write_csv(tables / "coverage.csv", ["system_id", "program", "repeat", "covered", "total"], [list(r.values()) for r in rows])


def _wrong_covered(tables):
    rows = read_csv(tables / "coverage.csv")
    rows[0]["covered"] = str(int(rows[0]["covered"]) + 1)
    write_csv(tables / "coverage.csv", ["system_id", "program", "repeat", "covered", "total"], [list(r.values()) for r in rows])


def _unsorted_set(tables):
    path = next((tables / "sets").rglob("*.json.gz"))
    doc = json.loads(gzip.decompress(path.read_bytes()))
    doc["covered_branch_keys"].reverse()
    path.write_bytes(gzip.compress(json.dumps(doc).encode()))


def _set_missing(tables):
    next((tables / "sets").rglob("*.json.gz")).unlink()


def _falling_curve(tables):
    path = next((tables / "curves").rglob("*.csv"))
    lines = path.read_text().splitlines()
    a, b = lines[5].split(","), lines[6].split(",")
    lines[6] = f"{b[0]},{int(a[1]) - 1}"
    path.write_text("\n".join(lines) + "\n")


def _curve_that_ends_elsewhere(tables):
    path = next((tables / "curves").rglob("*.csv"))
    lines = path.read_text().splitlines()
    t, c = lines[-1].split(",")
    lines[-1] = f"{t},{int(c) + 1}"
    path.write_text("\n".join(lines) + "\n")


def _other_profile(tables):
    path = tables / "replay-provenance.json"
    doc = json.loads(path.read_text())
    doc["values"]["profile"] = {"canonical-2026": 1}
    path.write_text(json.dumps(doc))


def _unknown_system(tables):
    rows = read_csv(tables / "runs.csv")
    old, prog, rep = rows[0]["system_id"], rows[0]["program"], rows[0]["repeat"]
    rows[0]["system_id"] = "invented-system"
    write_csv(tables / "runs.csv", ["system_id", "program", "repeat", "cell", "state"], [list(r.values()) for r in rows])
    rows = read_csv(tables / "coverage.csv")
    for r in rows:
        if (r["system_id"], r["program"], r["repeat"]) == (old, prog, rep):
            r["system_id"] = "invented-system"
    write_csv(tables / "coverage.csv", ["system_id", "program", "repeat", "covered", "total"], [list(r.values()) for r in rows])
    for folder, suffix in (("sets", ".json.gz"), ("curves", ".csv")):
        target = tables / folder / "invented-system" / prog / f"r{rep}{suffix}"
        target.parent.mkdir(parents=True)
        target.write_bytes((tables / folder / old / prog / f"r{rep}{suffix}").read_bytes())


def _run_without_coverage_row(tables):
    rows = read_csv(tables / "coverage.csv")
    write_csv(tables / "coverage.csv", ["system_id", "program", "repeat", "covered", "total"], [list(r.values()) for r in rows[1:]])


BAD_TABLES = {
    "a total that is not the program's": (_wrong_total, "programs.csv says"),
    "a covered count that is not the set's": (_wrong_covered, "coverage.csv says"),
    "a set with its keys out of order": (_unsorted_set, "keys are not sorted"),
    "a done run without its set": (_set_missing, "branch set"),
    "a curve that falls": (_falling_curve, "the curve falls"),
    "a curve that ends away from the run": (_curve_that_ends_elsewhere, "the curve ends at"),
    "another profile than the standard one": (_other_profile, "profile"),
    "a system the release does not know": (_unknown_system, "does not list"),
    "a done run with no coverage row": (_run_without_coverage_row, "has no row"),
}


@pytest.mark.parametrize("name", sorted(BAD_TABLES))
def test_tables_that_disagree_are_refused_before_anything_is_changed(world, name):
    """I2. The replay's tables are checked against each other and against the release; the release stays as it was."""

    damage, reason = BAD_TABLES[name]
    damage(world["tables"])
    refreshed_manifest(world["tables"])  # the files are what the manifest says, so only the content is wrong
    before = digest_tree(world["repo"])
    done = run_script(world["repo"], "import_replay.py", world["tables"], "--clean")
    assert done.returncode != 0, done.stdout
    assert reason in done.stdout + done.stderr, done.stdout + done.stderr
    assert digest_tree(world["repo"]) == before


def test_a_file_that_changed_after_the_replay_wrote_its_manifest_is_refused(world):
    """I2. The manifest is the replay's own statement of what it wrote."""

    path = next((world["tables"] / "curves").rglob("*.csv"))
    path.write_text(path.read_text() + "\n")
    before = digest_tree(world["repo"])
    done = run_script(world["repo"], "import_replay.py", world["tables"], "--clean")
    assert done.returncode != 0 and "differs from MANIFEST" in done.stdout + done.stderr
    assert digest_tree(world["repo"]) == before


def test_an_import_does_not_write_over_a_release_unless_asked(world):
    """I3. Without --clean the earlier output stays and the import stops."""

    before = digest_tree(world["repo"])
    assert run_script(world["repo"], "import_replay.py", world["tables"]).returncode != 0
    assert digest_tree(world["repo"]) == before


def test_the_sets_it_writes_are_the_same_bytes_every_time():
    """I4. Compact JSON, no build time in the gzip header: two imports of one replay give identical files."""

    doc = {"schema": import_replay.SCHEMA_SET, "program": "x", "covered_branch_keys": ["a:1:0:0"]}
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        first, second = Path(tmp) / "a.json.gz", Path(tmp) / "b.json.gz"
        import_replay.write_gz_json(first, doc)
        import_replay.write_gz_json(second, doc)
        assert first.read_bytes() == second.read_bytes()
        assert first.read_bytes()[4:8] == b"\x00\x00\x00\x00"  # the gzip header holds no time
        assert gzip.decompress(first.read_bytes()) == b'{"schema":"agent-software-testing/branch-set@0.1","program":"x","covered_branch_keys":["a:1:0:0"]}'


def test_the_image_is_named_by_its_digest_and_never_by_the_path_it_lived_at():
    digest = "a" * 64
    assert import_replay.image_sha256(f"/cluster/home/someone/images/runner.tar sha256={digest} record sha256={'b' * 64}") == digest
    with pytest.raises(SystemExit):
        import_replay.image_sha256("/cluster/home/someone/images/runner.tar")


def test_the_cleanup_never_removes_a_file_outside_the_repository(tmp_path):
    victim = tmp_path / "file"
    victim.write_text("keep")
    with pytest.raises(SystemExit):
        import_replay.remove_inside(victim)
    assert victim.read_text() == "keep"
