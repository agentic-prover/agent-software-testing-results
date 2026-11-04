# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Agent Software Testing authors
"""Promise T1 and T2 (tests/README.md): the tables people read (medians, overlaps) are computed from the runs by one
rule, and that rule gives the numbers worked out by hand.
"""
from __future__ import annotations

import gzip
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import derive  # noqa: E402


def write_set(root: Path, path: str, keys: list[str]) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    doc = {"schema": "agent-software-testing/branch-set@0.1", "program": "p", "covered_branch_keys": sorted(keys)}
    with gzip.open(target, "wb") as fh:
        fh.write(json.dumps(doc).encode())


def run_row(system, repeat, covered, *, notes="", program="p", total="10", sets=True):
    return {"system_id": system, "program": program, "repeat": str(repeat), "covered": str(covered), "total": total,
            "set_path": f"sets/{system}/{program}/r{repeat}.json.gz" if sets else "", "notes": notes}


@pytest.fixture
def small_release(tmp_path, monkeypatch):
    """Four systems on one program. a and b have repeats 1 to 3 (b also a fourth), c has two, d is marked incomplete."""

    monkeypatch.setattr(derive, "ROOT", tmp_path)
    k = [f"f.c:{i}:0:0" for i in range(1, 30)]
    sets = {
        ("a", 1): k[0:3], ("a", 2): k[0:6], ("a", 3): k[1:6],
        ("b", 1): k[0:3], ("b", 2): [k[2]] + k[6:12], ("b", 3): k[0:2], ("b", 4): k[20:29],
        ("c", 1): k[0:3], ("c", 2): k[0:4],
        ("d", 1): k[0:2], ("d", 2): k[0:2], ("d", 3): k[0:2],
    }
    runs = []
    for (system, repeat), keys in sets.items():
        notes = "incomplete" if system == "d" else ""
        runs.append(run_row(system, repeat, len(keys), notes=notes))
        write_set(tmp_path, runs[-1]["set_path"], keys)
    programs = [{"program": "p", "branch_total": "10"}]
    systems = [{"system_id": s} for s in "abcd"]
    return runs, programs, systems


def test_finals_are_the_median_minimum_and_maximum_of_the_runs_of_a_system_on_a_program(small_release):
    """T1. An odd count has its middle value, an even count the mean of the two middle ones (shown with .5 when it is
    not whole); the percentage is of the program's branch total."""

    runs, programs, systems = small_release
    assert derive.finals(runs, programs, systems) == [
        ["a", "p", 3, "5", 3, 6, 10, "50.00"],  # 3, 5, 6: the middle one, not the mean 4.67
        ["b", "p", 4, "5", 2, 9, 10, "50.00"],  # 2, 3, 7, 9: the mean of 3 and 7, not the mean 5.25
        ["c", "p", 2, "3.5", 3, 4, 10, "35.00"],  # 3, 4
        ["d", "p", 3, "2", 2, 2, 10, "20.00"],
    ]


def test_overlap_compares_the_unions_of_repeats_one_to_three_of_complete_pairs_only(small_release):
    """T1. a and b: the union of a is 6 branches, of b (repeats 1 to 3; the fourth is not used) 9, they share 3.
    c has two repeats and d is marked incomplete, so neither is compared."""

    runs, programs, systems = small_release
    rows, skipped = derive.overlap(runs, programs, systems)
    assert rows == [["p", "a", "b", 3, 6, 3]]
    assert skipped == []


def test_runs_counted_on_another_universe_than_the_program_are_not_mixed_in(small_release):
    runs, programs, systems = small_release
    odd = [dict(r, total="11") if (r["system_id"], r["repeat"]) == ("b", "2") else r for r in runs]
    with pytest.raises(SystemExit):
        derive.finals(odd, programs, systems)
    rows, skipped = derive.overlap(odd, programs, systems)
    assert rows == [] and skipped == [("b", "p")]


def test_regenerating_the_derived_files_gives_the_published_ones(tmp_path):
    """T2. derive.py run on the release rebuilds finals, overlap and the README table byte for byte, whatever was
    in them before; nothing published depends on a step that was done by hand."""

    root = tmp_path / "copy"
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "tests"))
    published = {name: (root / name).read_bytes() for name in ("derived/finals.csv", "derived/overlap.csv", "README.md", "derived/signatures.csv")}
    (root / "derived" / "finals.csv").write_text("system_id\nstale\n")
    (root / "derived" / "overlap.csv").write_text("program\nstale\n")
    readme = (root / "README.md").read_text()
    head, rest = readme.split(derive.README_START, 1)
    (root / "README.md").write_text(head + derive.README_START + "\nstale\n" + derive.README_END + rest.split(derive.README_END, 1)[1])
    done = subprocess.run([sys.executable, "-I", str(root / "scripts" / "derive.py")], capture_output=True, text=True, timeout=300,
                          env={"PATH": "/usr/bin:/bin"})
    assert done.returncode == 0, done.stdout + done.stderr
    for name, original in published.items():
        assert (root / name).read_bytes() == original, name
