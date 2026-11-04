#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Agent Software Testing authors
"""Add the sanitiser counts of one system on one program to derived/signatures.csv (standard library only).

    python3 scripts/add_signatures.py SYSTEM_ID PROGRAM SANITISER_OUTPUT_DIR [SANITISER_OUTPUT_DIR ...]
    python3 scripts/add_signatures.py SYSTEM_ID PROGRAM --rows signature-rows.csv

Give one output folder of the sanitiser pass for each repeat. A signature is counted once over the repeats, by its kind
and top frame, read from the private files that stay with the maintainers; only the kinds and counts are written. A
sanitiser that ran and found nothing gets one `none` row with zero signatures. A pass over one build that holds both
sanitisers writes `signatures-counts.json`; a pass for one sanitiser writes `signatures-asan-counts.json` or
`signatures-ubsan-counts.json`.

With `--rows FILE` the rows come from the `signature-rows.csv` that the replay workflow made on its runner (it counted
each signature once over the repeats there, so no private file leaves the runner). The file came through an artifact,
so its columns, sanitisers, kinds and counts are checked before anything is written.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from import_replay import KIND_SPELLING, SANITIZER_NAME  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HEADER = ["system_id", "program", "sanitizer", "kind", "signatures", "runs"]


def read_output(folder: Path):
    """The sanitisers that ran in one output folder and the (sanitiser, kind, frame) of every signature it found."""
    ran, found = set(), set()
    for name, sanitisers in (("signatures-counts.json", ("asan", "ubsan")), ("signatures-asan-counts.json", ("asan",)),
                             ("signatures-ubsan-counts.json", ("ubsan",))):
        counts = folder / name
        if not counts.is_file():
            continue
        ran.update(sanitisers)
        private = next((p for p in (folder / name.replace("counts", "private"), folder / "private" / name.replace("counts", "private"))
                        if p.is_file()), None)
        if private is None:
            if json.loads(counts.read_text())["signatures"]:
                raise SystemExit(f"{counts} reports signatures but its private file is missing")
            continue
        for sig in json.loads(private.read_text())["signatures"]:
            found.add((sig["sanitizer"], sig["kind"], sig["frame"]))
    if not ran:
        raise SystemExit(f"{folder} holds no sanitiser counts")
    return ran, found


def rows_for(system: str, program: str, folders: list[Path]) -> list[list]:
    runs = defaultdict(int)
    frames = defaultdict(set)
    for folder in folders:
        ran, found = read_output(folder)
        for san in ran:
            runs[san] += 1
        for san, kind, frame in found:
            if kind not in KIND_SPELLING:
                raise SystemExit(f"{folder}: unknown error kind {kind!r}; add its published spelling to KIND_SPELLING in import_replay.py")
            frames[san, kind].add(frame)
    rows = []
    for san in sorted(runs):
        kinds = sorted(kind for s, kind in frames if s == san)
        rows += [[system, program, SANITIZER_NAME[san], KIND_SPELLING[k], len(frames[san, k]), runs[san]] for k in kinds]
        if not kinds:
            rows.append([system, program, SANITIZER_NAME[san], "none", 0, runs[san]])
    return rows


def read_rows_file(path: Path, system: str, program: str) -> list[list]:
    """The rows of a signature-rows.csv from the replay workflow, after checking every field. SystemExit on a problem."""
    try:
        with open(path, newline="", encoding="utf-8") as handle:
            table = list(csv.reader(handle))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise SystemExit(f"{path} cannot be read ({type(exc).__name__})")
    if not table or table[0] != HEADER:
        raise SystemExit(f"{path} does not have the columns {','.join(HEADER)}")
    if len(table) == 1:
        raise SystemExit(f"{path} has no rows")
    sanitisers = set(SANITIZER_NAME.values())
    kinds = set(KIND_SPELLING.values()) | {"none"}
    rows, seen = [], set()
    for number, row in enumerate(table[1:], start=2):
        if len(row) != len(HEADER):
            raise SystemExit(f"{path} line {number}: expected {len(HEADER)} fields")
        sid, prog, san, kind, count, runs = row
        if sid != system or prog != program:
            raise SystemExit(f"{path} line {number}: the system or program is not {system} and {program}")
        if san not in sanitisers:
            raise SystemExit(f"{path} line {number}: unknown sanitiser")
        if kind not in kinds:
            raise SystemExit(f"{path} line {number}: unknown error kind; add its published spelling to KIND_SPELLING in import_replay.py")
        if not (count.isascii() and count.isdigit() and runs.isascii() and runs.isdigit()):
            raise SystemExit(f"{path} line {number}: the counts must be whole numbers")
        count, runs = int(count), int(runs)
        if runs < 1 or (kind == "none" and count != 0) or (kind != "none" and count < 1):
            raise SystemExit(f"{path} line {number}: the counts do not fit the kind")
        if (san, kind) in seen:
            raise SystemExit(f"{path} line {number}: {san} {kind} occurs twice")
        seen.add((san, kind))
        rows.append([sid, prog, san, kind, count, runs])
    return rows


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    system, program = argv[0], argv[1]
    if "--rows" in argv:
        if len(argv) != 4 or argv[2] != "--rows":
            print(__doc__, file=sys.stderr)
            return 2
        folders = None
    else:
        folders = [Path(a) for a in argv[2:]]
    path = ROOT / "derived/signatures.csv"
    with open(path, newline="") as handle:
        existing = list(csv.DictReader(handle))
    if any(r["system_id"] == system and r["program"] == program for r in existing):
        raise SystemExit(f"derived/signatures.csv already has {system} on {program}; correct those rows by hand")
    rows = read_rows_file(Path(argv[3]), system, program) if folders is None else rows_for(system, program, folders)
    with open(path, "a", newline="") as handle:
        csv.writer(handle, lineterminator="\n").writerows(rows)
    print(f"derived/signatures.csv: {len(rows)} rows added for {system} on {program}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
