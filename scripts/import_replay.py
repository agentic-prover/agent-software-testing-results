#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Agent Software Testing authors
"""Import one standard replay's tables into this repository.

    python scripts/import_replay.py <tables> [--clean]

`<tables>` is the folder that a standard replay writes (runs.csv, coverage.csv,
replay-provenance.json, signatures-by-system.csv, sets/, curves/, MANIFEST.sha256). Only runs whose state in the
tables' runs.csv is `done` enter the release; a run that was not replayed is left out and no earlier number is kept
beside the new one. Systems with no replayed run, and tools with no system left, are left out too. The identity of a
system (label, family, backend, model, resources, description, tool, version, configuration, dates, status) is the
row of systems.csv already in this repository; the importer writes that row for the systems the replay covers and
changes only its `measure` column. Every number in the data comes from the tables.

What it writes
    runs.csv            one row per replayed run: covered and total from coverage.csv, measure `standard`, the set
                        and curve paths, output_tokens from the runs.csv this repository held before, no notes
    sets/               the covered branch keys of each run, compact JSON in gzip
    curves/             t_s,covered at the 900 s checkpoints
    systems.csv         the systems with a run, `measure` = standard
    tools.csv           the tools those systems use
    derived/signatures.csv          from signatures-by-system.csv, sanitised spellings as published before
    derived/measure_agreement.csv   header only (one measure)
    derived/finals.csv, derived/overlap.csv   from derive.py
    RELEASE.json        release 2026.1, version 0.1.0, public true, counts recomputed

programs.csv and universe/ are not written: the importer checks that every replayed total equals the branch total of
programs.csv and stops with a message when it does not.

`--clean` removes the files this script writes, once the whole input has been read and checked. Without it the script
refuses to write over existing output. Only the Python standard library is used.

Usage:  python scripts/import_replay.py <tables> [--clean]
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCHEMA_SET = "agent-software-testing/branch-set@0.1"
KEY_FORMAT = "file:line:block_id:branch_id"
CHECKPOINTS = list(range(0, 21601, 900))
RUNS_HEADER = ["system_id", "program", "repeat", "covered", "total", "measure", "set_path", "curve_path",
               "output_tokens", "notes"]
AGREEMENT_HEADER = ["system_id", "program", "repeat", "measure_used", "measure_other", "only_used", "only_other",
                    "note"]
SIGNATURE_HEADER = ["system_id", "program", "sanitizer", "kind", "signatures", "runs"]

# The measure that a standard replay makes, and the profile its provenance must name.
MEASURE = "standard"
PROFILE = "standard"

# The release this import makes. The release identity is a choice, not a number read from the replay.
RELEASE = {
    "name": "Agent Software Testing results",
    "version": "0.1.0",
    "date": "2026-11-02",
    "release": "2026.1",
    "previous_release": None,
    "suite": None,
    "specification": "0.1",
    "programs_tag": None,
    "schemas": {"branch_set": "agent-software-testing/branch-set@0.1"},
    "public": True,
    "licences": {"data": "CC-BY-4.0", "scripts": "Apache-2.0"},
    "citation": ('Koh, Noller, Pasareanu and Sun, "Agentic Planning for Symbolic Execution", '
                 "arXiv 2608.06397 (2026)"),
}

# Sanitiser names and error kinds in the spellings that derived/signatures.csv has published: the sanitiser's name
# as ASan or UBSan, and the error class in words. The replay table gives the class of a coarser kind; where an older
# spelling exists it is used (left-shift-negative -> left shift of a negative value), and a coarser class without an
# older spelling keeps its own words (misaligned-access -> misaligned access). The site turns a phrase into a slug.
SANITIZER_NAME = {"asan": "ASan", "ubsan": "UBSan"}
KIND_SPELLING = {
    "unreachable": "unreachable",
    # AddressSanitizer
    "segv": "SEGV",
    "heap-buffer-overflow": "heap-buffer-overflow",
    "heap-use-after-free": "heap-use-after-free",
    "double-free": "double-free",
    "global-buffer-overflow": "global-buffer-overflow",
    "unknown-crash": "unknown-crash",
    "calloc-overflow": "calloc-overflow",
    "param-overlap": "parameter overlap",
    # UndefinedBehaviorSanitizer
    "misaligned-access": "misaligned access",
    "pointer-overflow": "pointer overflow",
    "null-pointer-use": "null pointer use",
    "left-shift-negative": "left shift of a negative value",
    "left-shift-overflow": "left shift out of range",
    "shift-exponent": "shift exponent too large",
    "float-cast-overflow": "value outside the range of its type",
    "signed-integer-overflow": "signed integer overflow",
    "index-out-of-bounds": "array index out of bounds",
    "nonnull-argument": "null pointer passed as a non-null argument",
    "division-by-zero": "division by zero",
    "unsigned-integer-overflow": "unsigned integer overflow",
    "negation-overflow": "negation overflow",
    "implicit-conversion": "implicit conversion",
    "invalid-value-load": "invalid value load",
    "invalid-builtin": "invalid builtin",
    "insufficient-object-space": "insufficient object space",
    "function-type-mismatch": "function type mismatch",
    "missing-return": "missing return",
    "nonnull-return": "null pointer returned as a non-null value",
    "vla-bound": "variable-length array bound",
    "bad-downcast": "bad downcast",
    "bad-vptr": "bad vptr",
    # The other kinds keep their own names, as the sanitiser spells them
    **{kind: kind for kind in (
        "abrt", "alloc-dealloc-mismatch", "allocation-size-too-big", "bad-free", "bad-malloc-usable-size", "bus",
        "container-overflow", "deadlysignal", "dynamic-stack-buffer-overflow", "failed-to-allocate", "fpe", "ill",
        "invalid-aligned-alloc-alignment", "invalid-allocation-alignment", "invalid-pointer-pair",
        "invalid-posix-memalign-alignment", "negative-size-param", "new-delete-type-mismatch", "odr-violation",
        "other", "out-of-memory", "pvalloc-overflow", "reallocarray-overflow", "stack-buffer-overflow",
        "stack-buffer-underflow", "stack-overflow", "stack-use-after-return", "stack-use-after-scope", "trap",
        "use-after-poison",
    )},
}
VERDICTS = {"exact", "differs", "differs (incomplete)", "count-equal", "count-differs"}

# Files this script writes. --clean removes exactly these, inside this repository.
WRITTEN_FILES = ["runs.csv", "systems.csv", "tools.csv", "RELEASE.json",
                 "derived/signatures.csv", "derived/measure_agreement.csv",
                 "derived/finals.csv", "derived/overlap.csv"]
WRITTEN_DIRS = ["sets", "curves"]


def stop(msg):
    raise SystemExit(f"import_replay: {msg}")


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return list(reader), reader.fieldnames or []


def write_csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def write_gz_json(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(doc, separators=(",", ":")).encode()
    with open(path, "wb") as fh:
        with gzip.GzipFile(filename="", mode="wb", fileobj=fh, mtime=0, compresslevel=9) as gz:
            gz.write(data)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verify_manifest(tables):
    """Every file the replay's manifest lists exists and has its digest. The manifest does not list itself."""
    manifest = tables / "MANIFEST.sha256"
    if not manifest.is_file():
        stop(f"{manifest} is missing")
    checked = 0
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, name = line.split(None, 1)
        name = name.strip()
        path = tables / name
        if not path.is_file():
            stop(f"{name} is listed in MANIFEST.sha256 but missing")
        if sha256_of(path) != digest:
            stop(f"{name} differs from MANIFEST.sha256")
        checked += 1
    return checked


def one(value_map, key):
    """The single value of a provenance map, or a clear stop."""
    values = value_map.get(key) or {}
    if len(values) != 1:
        stop(f"replay-provenance.json: expected one {key}, found {len(values)}")
    return next(iter(values))


def image_sha256(value):
    """The SHA-256 of the replay image, which is the first sha256= in the provenance's image value; the value also
    names a cluster path and the digest of a record file, and neither is published."""
    m = re.search(r"\bsha256=([0-9a-f]{64})\b", value)
    if not m:
        stop(f"replay-provenance.json: no image sha256 in {value!r}")
    return m.group(1)


def remove_inside(path):
    """Remove one file that is inside the repository; never follow a path out of it."""
    resolved = path.resolve()
    if resolved != ROOT and ROOT not in resolved.parents:
        stop(f"refusing to remove {resolved}, which is outside {ROOT}")
    if resolved.is_file():
        resolved.unlink()


def clean_written():
    """Remove the files this script writes, and empty the directories it writes, all inside the repository."""
    for name in WRITTEN_FILES:
        p = ROOT / name
        if p.exists():
            remove_inside(p)
    for name in WRITTEN_DIRS:
        top = ROOT / name
        if not top.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(top, topdown=False):
            for fn in filenames:
                remove_inside(Path(dirpath) / fn)
            if not os.listdir(dirpath):
                os.rmdir(dirpath)


def existing_outputs():
    found = [name for name in WRITTEN_FILES if (ROOT / name).is_file()]
    for name in WRITTEN_DIRS:
        if (ROOT / name).is_dir() and any((ROOT / name).rglob("*")):
            found.append(name)
    return found


def load_set(tables, sid, prog, rep, total):
    path = tables / "sets" / sid / prog / f"r{rep}.json.gz"
    if not path.is_file():
        stop(f"{path.relative_to(tables)}: the run is done but has no branch set")
    try:
        with gzip.open(path, "rb") as fh:
            doc = json.loads(fh.read())
    except (OSError, ValueError) as exc:
        stop(f"{path.relative_to(tables)}: {exc}")
    if list(doc) != ["schema", "program", "branch_key_format", "branch_total", "branch_covered",
                     "covered_branch_keys"]:
        stop(f"{path.relative_to(tables)}: fields {list(doc)}")
    if doc["schema"] != SCHEMA_SET or doc["branch_key_format"] != KEY_FORMAT:
        stop(f"{path.relative_to(tables)}: schema or key format")
    if doc["program"] != prog:
        stop(f"{path.relative_to(tables)}: program {doc['program']!r}, not {prog!r}")
    keys = doc["covered_branch_keys"]
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        stop(f"{path.relative_to(tables)}: keys are not sorted and unique")
    if doc["branch_total"] != total:
        stop(f"{path.relative_to(tables)}: branch_total {doc['branch_total']} but programs.csv says {total}")
    if doc["branch_covered"] != len(keys):
        stop(f"{path.relative_to(tables)}: branch_covered {doc['branch_covered']} but {len(keys)} keys")
    return doc


def load_curve(tables, sid, prog, rep, covered, total):
    path = tables / "curves" / sid / prog / f"r{rep}.csv"
    if not path.is_file():
        stop(f"{path.relative_to(tables)}: the run is done but has no curve")
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    if not rows or rows[0] != ["t_s", "covered"]:
        stop(f"{path.relative_to(tables)}: header is not t_s,covered")
    try:
        t = [int(x[0]) for x in rows[1:]]
        c = [int(x[1]) for x in rows[1:]]
    except (ValueError, IndexError):
        stop(f"{path.relative_to(tables)}: a row is not two integers")
    if t != CHECKPOINTS:
        stop(f"{path.relative_to(tables)}: not the 900 s checkpoints")
    if any(a > b for a, b in zip(c, c[1:])):
        stop(f"{path.relative_to(tables)}: the curve falls between two checkpoints")
    if min(c) < 0 or max(c) > total:
        stop(f"{path.relative_to(tables)}: a value is outside 0..{total}")
    if c[-1] != covered:
        stop(f"{path.relative_to(tables)}: the curve ends at {c[-1]} but the run covers {covered}")
    return [f"{x[0]},{x[1]}" for x in rows[1:]]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tables", help="the folder that the standard replay wrote")
    ap.add_argument("--clean", action="store_true", help="remove the earlier output of this script first")
    args = ap.parse_args()
    tables = Path(args.tables)
    if not tables.is_dir():
        stop(f"{tables} is not a folder")
    _main(tables, args.clean)
    return 0


def _main(tables, clean):
    # ---- read the tables and check them before anything in the repository is touched -----------------
    n_manifest = verify_manifest(tables)
    state_rows, state_header = read_csv(tables / "runs.csv")
    if state_header != ["system_id", "program", "repeat", "cell", "state"]:
        stop(f"runs.csv: header {state_header}")
    done = []
    not_done = 0
    for r in state_rows:
        if r["state"] == "done":
            done.append((r["system_id"], r["program"], int(r["repeat"])))
        elif r["state"] in ("missing", "failed"):
            not_done += 1
        else:
            stop(f"runs.csv: {r['cell']} has state {r['state']!r}")
    if len(done) != len(set(done)):
        stop("runs.csv: a run is listed twice")
    done = sorted(set(done))

    coverage, _ = read_csv(tables / "coverage.csv")
    cov = {}
    for r in coverage:
        key = (r["system_id"], r["program"], int(r["repeat"]))
        if key in cov:
            stop(f"coverage.csv: {key} is listed twice")
        cov[key] = r
    missing = set(done) - set(cov)
    extra = set(cov) - set(done)
    if missing:
        stop(f"coverage.csv has no row for {len(missing)} done runs, e.g. {sorted(missing)[0]}")
    if extra:
        stop(f"coverage.csv has rows for {len(extra)} runs that are not done, e.g. {sorted(extra)[0]}")

    provenance = json.loads((tables / "replay-provenance.json").read_text(encoding="utf-8"))
    values = provenance.get("values") or {}
    profile = one(values, "profile")
    if profile != PROFILE:
        stop(f"replay-provenance.json: profile {profile!r}, not {PROFILE!r}")
    runner_version = one(values, "runner_version")
    runner_manifest = one(values, "runner_manifest_sha256")
    image_sha = image_sha256(one(values, "image"))
    if provenance.get("done") is not None and int(provenance["done"]) != len(done):
        stop(f"replay-provenance.json says {provenance['done']} runs are done; runs.csv has {len(done)}")
    if provenance.get("runs") is not None and int(provenance["runs"]) != len(state_rows):
        stop(f"replay-provenance.json says {provenance['runs']} runs; runs.csv has {len(state_rows)}")

    # programs.csv and universe/ are kept; a replayed total must be the pinned build's branch total.
    programs, programs_header = read_csv(ROOT / "programs.csv")
    ptotal = {p["program"]: int(p["branch_total"]) for p in programs}
    porder = {p["program"]: i for i, p in enumerate(programs)}
    for key in done:
        if key[1] not in ptotal:
            stop(f"{key}: program {key[1]!r} is not in programs.csv")
        total = int(cov[key]["total"])
        if total != ptotal[key[1]]:
            stop(f"{key}: the replay counts {total} branches, programs.csv says {ptotal[key[1]]}")

    # output tokens of an earlier runs.csv belong to the same run; keep them where they were recorded.
    tokens = {}
    old_runs = ROOT / "runs.csv"
    if old_runs.is_file():
        old_rows, _ = read_csv(old_runs)
        tokens = {(r["system_id"], r["program"], int(r["repeat"])): r["output_tokens"] for r in old_rows
                  if r.get("output_tokens", "").isdigit()}

    # sets and curves, checked against coverage.csv, programs.csv and each other
    sets, curves, covered_of = {}, {}, {}
    for key in done:
        sid, prog, rep = key
        covered = int(cov[key]["covered"])
        if not 0 <= covered <= ptotal[prog]:
            stop(f"{key}: covered {covered} is outside 0..{ptotal[prog]}")
        doc = load_set(tables, sid, prog, rep, ptotal[prog])
        if doc["branch_covered"] != covered:
            stop(f"{key}: the set covers {doc['branch_covered']} branches, coverage.csv says {covered}")
        sets[key] = doc
        curves[key] = load_curve(tables, sid, prog, rep, covered, ptotal[prog])
        covered_of[key] = covered

    # systems.csv and tools.csv are the identity tables already in the repository; keep the rows the replay covers.
    systems, systems_header = read_csv(ROOT / "systems.csv")
    if systems_header[:12] != ["system_id", "label", "family", "backend", "model", "cores", "budget_s", "track",
                               "measure", "group", "description", "reference"]:
        stop(f"systems.csv: unexpected header {systems_header}")
    done_systems = {k[0] for k in done}
    known_systems = {s["system_id"] for s in systems}
    unknown = done_systems - known_systems
    if unknown:
        stop(f"the replay covers systems that systems.csv does not list: {sorted(unknown)}")
    kept_systems = []
    for s in systems:
        if s["system_id"] not in done_systems:
            continue
        row = dict(s)
        row["measure"] = MEASURE
        kept_systems.append(row)
    by_system = {s["system_id"]: s for s in kept_systems}
    for s in kept_systems:
        target = s.get("supersedes") or ""
        if target and target not in by_system:
            stop(f"systems.csv: {s['system_id']} supersedes {target}, which this replay leaves out")
        if s.get("status") == "superseded" and not any(x.get("supersedes") == s["system_id"] for x in kept_systems):
            stop(f"systems.csv: {s['system_id']} is superseded, but the system that supersedes it is left out")
    sorder = {s["system_id"]: i for i, s in enumerate(kept_systems)}

    tools, tools_header = read_csv(ROOT / "tools.csv")
    used_tools = {s["tool_id"] for s in kept_systems}
    known_tools = {t["tool_id"] for t in tools}
    if used_tools - known_tools:
        stop(f"systems.csv names tools that tools.csv does not list: {sorted(used_tools - known_tools)}")
    kept_tools = [t for t in tools if t["tool_id"] in used_tools]


    # derived/signatures.csv: sanitiser counts by system, program, sanitiser and error class. signatures-by-system.csv
    # gives the kinds and counts; signature-runs.csv gives the number of runs that were replayed, which is the same
    # for every kind of a system, program and sanitiser and is the `runs` column that the file has always published.
    # A sanitiser that was replayed and found nothing gets one `none` row with zero signatures.
    sig_runs, _ = read_csv(tables / "signature-runs.csv")
    replayed = Counter()
    replay_seen = set()
    pair_runs = Counter((k[0], k[1]) for k in done)
    for r in sig_runs:
        sid, prog = r["system_id"], r["program"]
        if (sid, prog) not in pair_runs:
            stop(f"signature-runs.csv: {sid} on {prog} has no done run")
        if r["sanitizer"] not in SANITIZER_NAME:
            stop(f"signature-runs.csv: unknown sanitiser {r['sanitizer']!r}")
        key = (sid, prog, int(r["repeat"]), SANITIZER_NAME[r["sanitizer"]])
        if key in replay_seen:
            stop(f"signature-runs.csv: {key} is listed twice")
        replay_seen.add(key)
        replayed[(sid, prog, key[3])] += 1
    sig_rows, _ = read_csv(tables / "signatures-by-system.csv")
    signatures = []
    seen_sig = set()
    for r in sig_rows:
        sid, prog = r["system_id"], r["program"]
        if (sid, prog) not in pair_runs:
            stop(f"signatures-by-system.csv: {sid} on {prog} has no done run")
        if r["sanitizer"] not in SANITIZER_NAME:
            stop(f"signatures-by-system.csv: unknown sanitiser {r['sanitizer']!r}")
        if r["kind"] not in KIND_SPELLING:
            stop(f"signatures-by-system.csv: unknown error kind {r['kind']!r}")
        san = SANITIZER_NAME[r["sanitizer"]]
        kind = KIND_SPELLING[r["kind"]]
        n = int(r["signatures"])
        if n < 1:
            stop(f"signatures-by-system.csv: {sid} {prog} {san} {kind} has {n} signatures")
        if replayed[(sid, prog, san)] < 1:
            stop(f"signatures-by-system.csv: {sid} {prog} {san} has a signature but signature-runs.csv has no run")
        if int(r["runs"]) > replayed[(sid, prog, san)]:
            stop(f"signatures-by-system.csv: {sid} {prog} {san} {kind} names {r['runs']} runs, "
                 f"signature-runs.csv has {replayed[(sid, prog, san)]}")
        key = (sid, prog, san, kind)
        if key in seen_sig:
            stop(f"signatures-by-system.csv: {key} is listed twice")
        seen_sig.add(key)
        signatures.append(key + (n, replayed[(sid, prog, san)]))
    for (sid, prog, san), n in replayed.items():
        if (sid, prog, san) not in {(k[0], k[1], k[2]) for k in signatures}:
            signatures.append((sid, prog, san, "none", 0, n))
    san_order = {"ASan": 0, "UBSan": 1}
    signatures.sort(key=lambda x: (sorder[x[0]], porder[x[1]], san_order[x[2]], x[3]))

    # ---- the release record and the counts ---------------------------------------------------------
    counts = {
        "systems": len(kept_systems),
        "programs": len(programs),
        "universes": len(list((ROOT / "universe").glob("*.txt.gz"))) if (ROOT / "universe").is_dir() else 0,
        "runs": len(done),
        "runs_with_branch_sets": len(done),
        "runs_with_curves": len(done),
    }
    release = dict(RELEASE)
    release["image"] = image_sha
    release["runner"] = f"agent-software-testing {runner_version}+{runner_manifest}"
    release["profile"] = profile
    release["counts"] = counts
    release["schemas"] = dict(RELEASE["schemas"])

    # ---- only now touch the repository -------------------------------------------------------------
    if clean:
        clean_written()
    else:
        existing = existing_outputs()
        if existing:
            stop(f"refusing to overwrite existing output: {existing} (use --clean)")

    for key in done:
        sid, prog, rep = key
        doc = sets[key]
        write_gz_json(ROOT / "sets" / sid / prog / f"r{rep}.json.gz",
                      {"schema": doc["schema"], "program": doc["program"],
                       "branch_key_format": doc["branch_key_format"], "branch_total": doc["branch_total"],
                       "branch_covered": doc["branch_covered"], "covered_branch_keys": doc["covered_branch_keys"]})
        path = ROOT / "curves" / sid / prog / f"r{rep}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("t_s,covered\n" + "\n".join(curves[key]) + "\n", encoding="utf-8")

    runs = []
    for key in sorted(done, key=lambda k: (sorder[k[0]], porder[k[1]], k[2])):
        sid, prog, rep = key
        runs.append(dict(system_id=sid, program=prog, repeat=rep, covered=covered_of[key],
                         total=int(cov[key]["total"]), measure=MEASURE,
                         set_path=f"sets/{sid}/{prog}/r{rep}.json.gz",
                         curve_path=f"curves/{sid}/{prog}/r{rep}.csv",
                         output_tokens=tokens.get(key, ""), notes=""))
    write_csv(ROOT / "runs.csv", RUNS_HEADER,
              [[r[k] for k in RUNS_HEADER] for r in runs])
    write_csv(ROOT / "systems.csv", systems_header, [[s[k] for k in systems_header] for s in kept_systems])
    write_csv(ROOT / "tools.csv", tools_header, [[t[k] for k in tools_header] for t in kept_tools])
    write_csv(ROOT / "derived" / "signatures.csv", SIGNATURE_HEADER, signatures)
    write_csv(ROOT / "derived" / "measure_agreement.csv", AGREEMENT_HEADER, [])

    # finals and overlap come from derive.py, so that the one rule lives in one place.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import derive  # noqa: E402
    finals = derive.finals(runs, programs, kept_systems)
    write_csv(ROOT / "derived" / "finals.csv",
              ["system_id", "program", "n_runs", "median", "min", "max", "total", "median_pct"], finals)
    overlap, skipped = derive.overlap(runs, programs, kept_systems)
    write_csv(ROOT / "derived" / "overlap.csv",
              ["program", "system_a", "system_b", "a_only", "b_only", "both"], overlap)
    for sid, prog in skipped:
        print(f"skipped (different universe): {sid} on {prog}", file=sys.stderr)

    (ROOT / "RELEASE.json").write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {len(runs)} runs, {len(kept_systems)} systems, {len(programs)} programs, {len(kept_tools)} tools "
          f"from {tables} ({not_done} runs not replayed are left out; {n_manifest} input files checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
