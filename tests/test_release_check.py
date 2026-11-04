# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Agent Software Testing authors
"""Promises R1 to R4 (tests/README.md): the release in this repository is consistent, and scripts/check.py refuses a
release that is not.

The tampering tests copy the whole release, change one thing the way a mistake or a bad actor would, run the copy's
own check and expect it to fail. Nothing here depends on a particular number in the data: each change is derived from
what the files hold.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# -- helpers ---------------------------------------------------------------------------------------------------------

BULK = ("sets", "curves", "universe")  # the folders that are most of the bytes; a tampered copy links to their files


def own(path: Path) -> Path:
    """Make a linked file a real one before it is changed, so that a change never reaches the shared copy."""
    if path.is_symlink():
        data = path.read_bytes()
        path.unlink()
        path.write_bytes(data)
    return path


def read_rows(root: Path, name: str):
    with open(root / name, newline="") as fh:
        rows = list(csv.reader(fh))
    return rows[0], rows[1:]


def write_rows(root: Path, name: str, header, rows) -> None:
    with open(own(root / name), "w", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def edit_text(root: Path, name: str, old: str, new: str) -> None:
    path = own(root / name)
    text = path.read_text()
    assert old in text, f"{name} does not contain {old!r}"
    path.write_text(text.replace(old, new, 1))


def late_run(root: Path):
    """A run of repeat 4 or later: it enters no median and no overlap, so changing its files isolates one check."""
    header, rows = read_rows(root, "runs.csv")
    row = next(r for r in rows if int(r[2]) >= 4)
    return dict(zip(header, row))


def load_set(root: Path, path: str) -> dict:
    with gzip.open(root / path, "rb") as fh:
        return json.loads(fh.read())


def save_set(root: Path, path: str, doc: dict) -> None:
    with gzip.open(own(root / path), "wb") as fh:
        fh.write(json.dumps(doc, separators=(",", ":")).encode())


def foreign_key_for(doc: dict, universe: set[str]) -> str:
    """A key that sorts after the last one, is well-formed and is in no universe."""
    last = doc["covered_branch_keys"][-1]
    stem = last.rsplit(":", 1)[0]
    key = f"{stem}:9999"
    assert key not in universe and key > last
    return key


# -- one function per tampering: each takes the root of a copy of the release -------------------------------------------


def a_covered_branch_that_is_not_in_the_program(root):
    run = late_run(root)
    doc = load_set(root, run["set_path"])
    with gzip.open(root / "universe" / f"{run['program']}.txt.gz", "rt") as fh:
        universe = set(fh.read().split("\n"))
    doc["covered_branch_keys"][-1] = foreign_key_for(doc, universe)
    save_set(root, run["set_path"], doc)


def a_set_whose_keys_are_out_of_order(root):
    run = late_run(root)
    doc = load_set(root, run["set_path"])
    doc["covered_branch_keys"][0], doc["covered_branch_keys"][1] = doc["covered_branch_keys"][1], doc["covered_branch_keys"][0]
    save_set(root, run["set_path"], doc)


def a_set_with_a_key_twice(root):
    run = late_run(root)
    doc = load_set(root, run["set_path"])
    doc["covered_branch_keys"][1] = doc["covered_branch_keys"][0]
    save_set(root, run["set_path"], doc)


def a_set_that_counts_more_than_it_lists(root):
    run = late_run(root)
    doc = load_set(root, run["set_path"])
    doc["branch_covered"] += 1
    save_set(root, run["set_path"], doc)


def a_set_of_another_program(root):
    run = late_run(root)
    doc = load_set(root, run["set_path"])
    doc["program"] = "not-" + doc["program"]
    save_set(root, run["set_path"], doc)


def a_set_that_lost_a_branch_the_run_table_still_counts(root):
    run = late_run(root)
    doc = load_set(root, run["set_path"])
    del doc["covered_branch_keys"][-1]
    doc["branch_covered"] -= 1
    save_set(root, run["set_path"], doc)


def a_run_table_that_disagrees_with_its_set(root):
    header, rows = read_rows(root, "runs.csv")
    target = late_run(root)
    for row in rows:
        if row[:3] == [target["system_id"], target["program"], target["repeat"]]:
            row[3] = str(int(row[3]) + 1)
    write_rows(root, "runs.csv", header, rows)


def a_run_that_points_at_the_set_of_another_run(root):
    header, rows = read_rows(root, "runs.csv")
    target = late_run(root)
    for row in rows:
        if row[:3] == [target["system_id"], target["program"], target["repeat"]]:
            row[header.index("set_path")] = rows[0][header.index("set_path")]
    write_rows(root, "runs.csv", header, rows)


def a_set_file_that_is_missing(root):
    (root / late_run(root)["set_path"]).unlink()


def a_file_in_sets_that_no_run_names(root):
    (root / "sets" / "extra-file.txt").write_text("not part of any run")


def a_curve_file_that_is_missing(root):
    (root / late_run(root)["curve_path"]).unlink()


def a_universe_with_a_branch_more(root):
    path = root / "universe" / f"{late_run(root)['program']}.txt.gz"
    with gzip.open(path, "rt") as fh:
        keys = fh.read().split("\n")[:-1]
    keys.append(keys[-1].rsplit(":", 1)[0] + ":9999")
    with gzip.open(own(path), "wt", newline="") as fh:
        fh.write("\n".join(keys) + "\n")


def a_universe_that_is_not_sorted(root):
    path = root / "universe" / f"{late_run(root)['program']}.txt.gz"
    with gzip.open(path, "rt") as fh:
        keys = fh.read().split("\n")[:-1]
    keys[0], keys[1] = keys[1], keys[0]
    with gzip.open(own(path), "wt", newline="") as fh:
        fh.write("\n".join(keys) + "\n")


def a_changed_branch_total_of_a_program(root):
    header, rows = read_rows(root, "programs.csv")
    column = header.index("branch_total")
    rows[0][column] = str(int(rows[0][column]) + 1)
    write_rows(root, "programs.csv", header, rows)


def a_run_with_another_total_than_its_program(root):
    header, rows = read_rows(root, "runs.csv")
    column = header.index("total")
    rows[-1][column] = str(int(rows[-1][column]) + 1)
    write_rows(root, "runs.csv", header, rows)


def a_curve_that_ends_away_from_the_run(root):
    path = own(root / late_run(root)["curve_path"])
    lines = path.read_text().splitlines()
    t, c = lines[-1].split(",")
    lines[-1] = f"{t},{int(c) + 1}"
    path.write_text("\n".join(lines) + "\n")


def a_curve_that_falls(root):
    path = own(root / late_run(root)["curve_path"])
    lines = path.read_text().splitlines()
    t, c = lines[10].split(",")
    lines[10] = f"{t},{max(0, int(c) - 50)}"
    path.write_text("\n".join(lines) + "\n")


def a_curve_with_a_large_dip_that_the_notes_state(root):
    target = late_run(root)
    header, rows = read_rows(root, "runs.csv")
    for row in rows:
        if row[:3] == [target["system_id"], target["program"], target["repeat"]]:
            row[header.index("notes")] = "curve dips by up to 50 branches"
    write_rows(root, "runs.csv", header, rows)
    path = own(root / target["curve_path"])
    lines = path.read_text().splitlines()
    t, before = lines[9].split(",")
    lines[10] = f"{lines[10].split(',')[0]},{int(before) - 50}"
    path.write_text("\n".join(lines) + "\n")


def a_curve_with_a_checkpoint_missing(root):
    path = own(root / late_run(root)["curve_path"])
    lines = path.read_text().splitlines()
    del lines[5]
    path.write_text("\n".join(lines) + "\n")


def a_final_that_no_longer_matches_the_runs(root):
    header, rows = read_rows(root, "derived/finals.csv")
    rows[0][header.index("median")] = str(int(float(rows[0][header.index("median")])) + 1)
    write_rows(root, "derived/finals.csv", header, rows)


def an_overlap_count_that_is_wrong(root):
    header, rows = read_rows(root, "derived/overlap.csv")
    rows[0][header.index("both")] = str(int(rows[0][header.index("both")]) + 1)
    write_rows(root, "derived/overlap.csv", header, rows)


def an_overlap_pair_that_is_missing(root):
    header, rows = read_rows(root, "derived/overlap.csv")
    write_rows(root, "derived/overlap.csv", header, rows[1:])


def a_signature_table_that_names_a_location(root):
    header, rows = read_rows(root, "derived/signatures.csv")
    rows[0][header.index("kind")] = "heap-buffer-overflow in src/main.c:120"
    write_rows(root, "derived/signatures.csv", header, rows)


def a_signature_table_with_a_frame_column(root):
    header, rows = read_rows(root, "derived/signatures.csv")
    write_rows(root, "derived/signatures.csv", header + ["frame"], [r + ["f"] for r in rows])


def a_signature_kind_that_is_not_a_published_class(root):
    header, rows = read_rows(root, "derived/signatures.csv")
    rows[0][header.index("kind")] = "invented class"
    write_rows(root, "derived/signatures.csv", header, rows)


def a_release_record_with_the_wrong_counts(root):
    path = root / "RELEASE.json"
    doc = json.loads(path.read_text())
    doc["counts"]["runs"] += 1
    path.write_text(json.dumps(doc, indent=2) + "\n")


def a_release_record_without_the_public_flag(root):
    path = root / "RELEASE.json"
    doc = json.loads(path.read_text())
    del doc["public"]
    path.write_text(json.dumps(doc, indent=2) + "\n")


def a_release_record_that_forgets_what_it_was_measured_with(root):
    path = root / "RELEASE.json"
    doc = json.loads(path.read_text())
    del doc["image"]
    path.write_text(json.dumps(doc, indent=2) + "\n")


def a_system_that_is_a_copy_of_another(root):
    header, rows = read_rows(root, "systems.csv")
    copy = list(rows[0])
    copy[0] = "copy-of-" + copy[0]
    write_rows(root, "systems.csv", header, rows + [copy])


def a_system_marked_superseded_that_nothing_supersedes(root):
    header, rows = read_rows(root, "systems.csv")
    current = next(r for r in rows if r[header.index("status")] == "current" and not any(x[header.index("supersedes")] == r[0] for x in rows))
    current[header.index("status")] = "superseded"
    write_rows(root, "systems.csv", header, rows)


def a_system_that_names_a_tool_nobody_defined(root):
    header, rows = read_rows(root, "systems.csv")
    rows[0][header.index("tool_id")] = "no-such-tool"
    write_rows(root, "systems.csv", header, rows)


def a_system_whose_measure_is_not_the_one_of_its_runs(root):
    header, rows = read_rows(root, "systems.csv")
    rows[0][header.index("measure")] = "canonical"
    write_rows(root, "systems.csv", header, rows)


def a_run_of_a_system_nobody_defined(root):
    header, rows = read_rows(root, "runs.csv")
    ghost = list(rows[0])
    ghost[0] = "ghost-system"
    write_rows(root, "runs.csv", header, [ghost] + rows)


def a_run_listed_twice(root):
    header, rows = read_rows(root, "runs.csv")
    write_rows(root, "runs.csv", header, rows[:1] + rows)


def runs_out_of_order(root):
    header, rows = read_rows(root, "runs.csv")
    rows[0], rows[1] = rows[1], rows[0]
    write_rows(root, "runs.csv", header, rows)


def a_tool_that_no_system_uses(root):
    header, rows = read_rows(root, "tools.csv")
    extra = list(rows[0])
    extra[0] = "unused-tool"
    write_rows(root, "tools.csv", header, rows + [extra])


def a_systems_table_in_the_readme_that_is_out_of_date(root):
    text = (root / "README.md").read_text()
    table = text.split("<!-- systems:start -->")[1].split("<!-- systems:end -->")[0]
    row = next(line for line in table.splitlines() if line.startswith("| `"))
    cells = row.split("|")
    cells[5] = " " + str(int(cells[5]) + 1) + " "
    edit_text(root, "README.md", row, "|".join(cells))


def a_readme_that_loses_the_line_on_what_is_not_published(root):
    edit_text(root, "README.md", "Locations, stack traces and inputs of bugs not yet fixed upstream are not published", "Locations are published")


def a_required_file_that_is_missing(root):
    (root / "LICENSE-DATA.md").unlink()


def a_script_without_its_licence_line(root):
    edit_text(root, "scripts/validate_manifest.py", "# SPDX-License-Identifier: Apache-2.0\n", "")


def a_submission_manifest_that_breaks_its_schema(root):
    path = root / "submissions" / "example.json"
    doc = json.loads(path.read_text())
    del doc["tool"]
    path.write_text(json.dumps(doc, indent=2))


TAMPERINGS = {fn.__name__.replace("_", " "): fn for fn in [
    a_covered_branch_that_is_not_in_the_program,
    a_set_whose_keys_are_out_of_order,
    a_set_with_a_key_twice,
    a_set_that_counts_more_than_it_lists,
    a_set_that_lost_a_branch_the_run_table_still_counts,
    a_set_of_another_program,
    a_run_table_that_disagrees_with_its_set,
    a_run_that_points_at_the_set_of_another_run,
    a_set_file_that_is_missing,
    a_file_in_sets_that_no_run_names,
    a_curve_file_that_is_missing,
    a_universe_with_a_branch_more,
    a_universe_that_is_not_sorted,
    a_changed_branch_total_of_a_program,
    a_run_with_another_total_than_its_program,
    a_curve_that_ends_away_from_the_run,
    a_curve_that_falls,
    a_curve_with_a_large_dip_that_the_notes_state,
    a_curve_with_a_checkpoint_missing,
    a_final_that_no_longer_matches_the_runs,
    an_overlap_count_that_is_wrong,
    an_overlap_pair_that_is_missing,
    a_signature_table_that_names_a_location,
    a_signature_table_with_a_frame_column,
    a_signature_kind_that_is_not_a_published_class,
    a_release_record_with_the_wrong_counts,
    a_release_record_without_the_public_flag,
    a_release_record_that_forgets_what_it_was_measured_with,
    a_system_that_is_a_copy_of_another,
    a_system_marked_superseded_that_nothing_supersedes,
    a_system_that_names_a_tool_nobody_defined,
    a_system_whose_measure_is_not_the_one_of_its_runs,
    a_run_of_a_system_nobody_defined,
    a_run_listed_twice,
    runs_out_of_order,
    a_tool_that_no_system_uses,
    a_systems_table_in_the_readme_that_is_out_of_date,
    a_readme_that_loses_the_line_on_what_is_not_published,
    a_required_file_that_is_missing,
    a_script_without_its_licence_line,
    a_submission_manifest_that_breaks_its_schema,
]}


def run_check(root: Path):
    done = subprocess.run([sys.executable, "-I", str(root / "scripts" / "check.py")], capture_output=True, text=True, timeout=300)
    assert "Traceback" not in done.stderr, done.stderr
    return done.returncode, done.stdout + done.stderr


def copy_release(destination: Path) -> Path:
    shutil.copytree(ROOT, destination, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "tests"))
    return destination


def digest_tree(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes())
    return h.hexdigest()


def linked_copy(shared: Path, destination: Path) -> Path:
    """A copy of the shared one in which the bulk folders hold links to its files and everything else is real."""
    shutil.copytree(shared, destination, ignore=shutil.ignore_patterns(*BULK))
    for folder in BULK:
        (destination / folder).mkdir()
        for path in sorted((shared / folder).rglob("*")):
            target = destination / path.relative_to(shared)
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.symlink_to(path)
    return destination


# Why each change must be refused: a second check must not hide a first one that stopped working.
REASONS = {
    "a covered branch that is not in the program": r"not in the universe",
    "a set whose keys are out of order": r"keys not sorted",
    "a set with a key twice": r"keys not sorted",
    "a set that counts more than it lists": r"covered \d+/\d+ vs",
    "a set that lost a branch the run table still counts": r"covered \d+/\d+ vs",
    "a set of another program": r"program not-",
    "a run table that disagrees with its set": r"vs runs\.csv \d+",
    "a run that points at the set of another run": r"set_path",
    "a set file that is missing": r"\.json\.gz: missing",
    "a file in sets that no run names": r"unreferenced file",
    "a curve file that is missing": r"\.csv: missing",
    "a universe with a branch more": r"programs.csv says",
    "a universe that is not sorted": r"keys not sorted",
    "a changed branch total of a program": r"totals differ from the specification",
    "a run with another total than its program": r"runs\.csv: .* total \d+ != \d+",
    "a curve that ends away from the run": r"ends at",
    "a curve that falls": r"dips by",
    "a curve with a large dip that the notes state": r"dips by",
    "a curve with a checkpoint missing": r"checkpoints do not follow",
    "a final that no longer matches the runs": r"finals.csv differs",
    "an overlap count that is wrong": r"wrong counts",
    "an overlap pair that is missing": r"pairs missing",
    "a signature table that names a location": r"looks like it contains",
    "a signature table with a frame column": r"derived/signatures\.csv: header",
    "a signature kind that is not a published class": r"not one of the published error classes",
    "a release record with the wrong counts": r"RELEASE.json counts",
    "a release record without the public flag": r"public",
    "a release record that forgets what it was measured with": r"is missing",
    "a system that is a copy of another": r"agree in tool",
    "a system marked superseded that nothing supersedes": r"no system supersedes",
    "a system that names a tool nobody defined": r"which tools.csv does not list",
    "a system whose measure is not the one of its runs": r"measure \w+ != \w+",
    "a run of a system nobody defined": r"unknown system",
    "a run listed twice": r"duplicate run",
    "runs out of order": r"not sorted by system",
    "a tool that no system uses": r"has no system",
    "a systems table in the readme that is out of date": r"out of date",
    "a readme that loses the line on what is not published": r"what is published",
    "a required file that is missing": r"LICENSE-DATA\.md",
    "a script without its licence line": r"SPDX",
    "a submission manifest that breaks its schema": r"submissions/example\.json",
}


@pytest.fixture(scope="module")
def shared_release(tmp_path_factory):
    """One real copy of the release, linked to by every tampered copy and checked at the end to be as it was."""

    shared = copy_release(tmp_path_factory.mktemp("shared") / "release")
    before = digest_tree(shared)
    yield shared
    assert digest_tree(shared) == before, "a tampering reached the shared copy"


@pytest.fixture(scope="module")
def tampered(shared_release, tmp_path_factory):
    """Every tampered copy is made and checked once, a few at a time, so that the whole table costs seconds."""

    base = tmp_path_factory.mktemp("tampered")

    def one(item):
        name, tamper = item
        root = linked_copy(shared_release, base / name.replace(" ", "-"))
        tamper(root)
        return name, run_check(root)

    with ThreadPoolExecutor(max_workers=6) as pool:
        return dict(pool.map(one, TAMPERINGS.items()))


# -- the tests -------------------------------------------------------------------------------------------------------------


def test_the_release_in_this_repository_passes_its_own_check():
    """R1. The data and the check agree; every later test starts from here."""

    code, output = run_check(ROOT)
    assert code == 0, output
    assert "all checks passed" in output


@pytest.mark.parametrize("name", sorted(TAMPERINGS))
def test_a_release_with_this_change_is_refused(tampered, name):
    """R2. A tampered, incomplete or inconsistent release fails the check, for the reason that fits the change."""

    code, output = tampered[name]
    assert code != 0, f"check.py accepted: {name}"
    assert re.search(REASONS[name], output), f"{name}: refused for another reason:\n{output[:800]}"


def test_every_change_says_why_it_must_be_refused():
    assert set(REASONS) == set(TAMPERINGS)


def test_the_readme_states_the_counts_of_the_tables():
    """R3. The first paragraph says how many systems, programs and runs there are; they are the ones in RELEASE.json."""

    text = " ".join((ROOT / "README.md").read_text().split())
    found = re.search(r"holds ([\d,]+) systems on ([\d,]+) programs, ([\d,]+) runs", text)
    assert found, "the README no longer states its counts in the form 'holds N systems on N programs, N runs'"
    counts = json.loads((ROOT / "RELEASE.json").read_text())["counts"]
    assert [int(g.replace(",", "")) for g in found.groups()] == [counts["systems"], counts["programs"], counts["runs"]]


def test_no_public_file_holds_a_crash_report():
    """R4. Counts of sanitiser findings are public; reports, stack frames and inputs are not (DISCLOSURE.md)."""

    markers = [r"ERROR: AddressSanitizer", r"runtime error:", r"SUMMARY: ", r"#\d+ 0x[0-9a-f]+ in ", r"==\d+==ERROR"]
    skipped = {"tests", "scripts", ".git", "__pycache__"}
    offenders = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or skipped & set(path.relative_to(ROOT).parts) or path.suffix == ".gz":
            continue
        text = path.read_text(errors="ignore")
        offenders += [f"{path.relative_to(ROOT)}: {m}" for m in markers if re.search(m, text)]
    assert offenders == []
    private = [p for p in ROOT.rglob("*") if "private" in p.name and p.is_file() and "tests" not in p.parts]
    assert private == []
