#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Agent Software Testing authors
"""Derive the tables in derived/ from runs.csv and the branch sets.

    derived/finals.csv      system_id,program,n_runs,median,min,max,total,median_pct
    derived/overlap.csv     program,system_a,system_b,a_only,b_only,both

It also refreshes the table of systems between the two markers in README.md.

Rules
  finals      all runs of a system on a program that have a covered count (runs.csv), whether or not a branch
              set is held. median_pct is the median as a percentage of the program's branch total.
  overlap     Unions over the runs of each system, compared on the branches both measurements count. Only
              programs where both systems have all their runs (a branch set for repeats 1, 2 and 3, and no run
              marked incomplete) enter; a system with more than three runs uses repeats 1 to 3. Two systems are
              compared on the intersection of their branch universes. Every run in release 0.1 is counted on the
              program's full pinned universe (programs.csv), so the intersection is that universe; a pair whose
              runs were counted on different universes is skipped and listed on standard error.
              Pairs are written once, with system_a before system_b in the order of systems.csv.
"""
from __future__ import annotations

import csv
import gzip
import json
import re
import statistics
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read_csv(name):
    with open(ROOT / name, newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(name, header, rows):
    path = ROOT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def fmt(x):
    return str(int(x)) if float(x).is_integer() else f"{x:.1f}"


def load_keys(set_path):
    with gzip.open(ROOT / set_path, "rb") as fh:
        return json.loads(fh.read())["covered_branch_keys"]


def finals(runs, programs, systems):
    ptotal = {p["program"]: int(p["branch_total"]) for p in programs}
    porder = {p["program"]: i for i, p in enumerate(programs)}
    sorder = {s["system_id"]: i for i, s in enumerate(systems)}
    group = defaultdict(list)
    for r in runs:
        group[(r["system_id"], r["program"])].append(r)
    rows = []
    for (sid, prog), rs in sorted(group.items(), key=lambda kv: (sorder[kv[0][0]], porder[kv[0][1]])):
        cov = [int(r["covered"]) for r in rs]
        totals = {int(r["total"]) for r in rs}
        if totals != {ptotal[prog]}:
            raise SystemExit(f"runs of {sid} on {prog} disagree with programs.csv on the total: {totals}")
        med = statistics.median(cov)
        rows.append([sid, prog, len(cov), fmt(med), min(cov), max(cov), ptotal[prog],
                     f"{100.0 * med / ptotal[prog]:.2f}"])
    return rows


def overlap(runs, programs, systems):
    ptotal = {p["program"]: int(p["branch_total"]) for p in programs}
    sorder = {s["system_id"]: i for i, s in enumerate(systems)}
    group = defaultdict(dict)
    incomplete = set()
    for r in runs:
        key = (r["system_id"], r["program"])
        if "incomplete" in r["notes"]:
            incomplete.add(key)
        if r["set_path"] and int(r["repeat"]) in (1, 2, 3):
            group[key][int(r["repeat"])] = r
    rows, skipped = [], []
    for p in programs:
        prog = p["program"]
        unions = {}
        for (sid, pr), by_rep in group.items():
            if pr != prog or sorted(by_rep) != [1, 2, 3] or (sid, pr) in incomplete:
                continue
            totals = {int(r["total"]) for r in by_rep.values()}
            if totals != {ptotal[prog]}:
                skipped.append((sid, prog))
                continue
            u = set()
            for r in by_rep.values():
                u.update(load_keys(r["set_path"]))
            unions[sid] = u
        for a, b in combinations(sorted(unions, key=lambda s: sorder[s]), 2):
            both = len(unions[a] & unions[b])
            rows.append([prog, a, b, len(unions[a]) - both, len(unions[b]) - both, both])
    return rows, skipped


README_START = "<!-- systems:start -->"
README_END = "<!-- systems:end -->"


def systems_table(runs, systems):
    """Markdown table of the systems with their numbers of programs, runs and branch sets."""
    lines = ["| System | Group | Family | Programs | Runs | Branch sets | Measure |",
             "|---|---|---|---:|---:|---:|---|"]
    for s in systems:
        rs = [r for r in runs if r["system_id"] == s["system_id"]]
        lines.append("| `{}` | {} | {} | {} | {} | {} | {} |".format(
            s["system_id"], s["group"], s["family"], len({r["program"] for r in rs}), len(rs),
            sum(1 for r in rs if r["set_path"]), s["measure"].replace("+", ", ")))
    return "\n".join(lines)


def update_readme(runs, systems):
    """Replace the text between the two markers of README.md by the current systems table."""
    path = ROOT / "README.md"
    text = path.read_text()
    if README_START not in text or README_END not in text:
        print("README.md has no systems markers; table not updated", file=sys.stderr)
        return
    head, rest = text.split(README_START, 1)
    _, tail = rest.split(README_END, 1)
    text = head + README_START + "\n" + systems_table(runs, systems) + "\n" + README_END + tail
    programs = {r["program"] for r in runs}
    counts = f"holds {len(systems)} systems on {len(programs)} programs, {len(runs)} runs"
    path.write_text(re.sub(r"holds \d+ systems on \d+ programs, \d+ runs", counts, text, count=1))


def main():
    programs = read_csv("programs.csv")
    systems = read_csv("systems.csv")
    runs = read_csv("runs.csv")

    rows = finals(runs, programs, systems)
    write_csv("derived/finals.csv", ["system_id", "program", "n_runs", "median", "min", "max", "total",
                                     "median_pct"], rows)
    print(f"derived/finals.csv: {len(rows)} rows")

    rows, skipped = overlap(runs, programs, systems)
    write_csv("derived/overlap.csv", ["program", "system_a", "system_b", "a_only", "b_only", "both"], rows)
    print(f"derived/overlap.csv: {len(rows)} rows")
    for sid, prog in skipped:
        print(f"skipped (different universe): {sid} on {prog}", file=sys.stderr)

    update_readme(runs, systems)

    return 0


if __name__ == "__main__":
    sys.exit(main())
