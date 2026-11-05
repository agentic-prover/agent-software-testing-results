#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Agent Software Testing authors
"""Check the results repository. Exit status 0 when every check passes.

Checks
  - required files exist and the tables have exactly the columns of the specification
  - programs.csv carries the branch totals of the pinned builds; systems.csv and runs.csv agree with each other
  - programs.csv agrees with the program definitions of the specification repository (version, source, input
    channels, fixed track, branch total) when that repository is checked out next to this one
  - universe/<program>.txt.gz: sorted unique well-formed keys, as many as the program's branch total
    (every program has one; NO_UNIVERSE would list a program without a gcovr-format source, none is listed)
  - every branch set: schema, sorted unique well-formed keys, covered count and total equal runs.csv, total equals
    programs.csv (a measure with a documented different universe would be listed in UNIVERSE_EXCEPTIONS; none is),
    and every covered key belongs to the program's universe (KEY_EXCEPTIONS would list documented exceptions;
    none is listed)
  - every curve: a checkpoint every 900 s up to the budget of its system, then the budget itself, non-decreasing (a dip is stated in the run's note and is at most
    10 branches; DIP_EXCEPTIONS lists the documented larger ones), ending at the run's covered count or at the value
    its note gives
  - every path in runs.csv exists and no file in sets/ or curves/ is unreferenced
  - derived/finals.csv equals a recomputation; derived/overlap.csv agrees with the sets and holds exactly the
    pairs the rule admits; derived/signatures.csv holds only counts by error class, for every program, and fails on a
    location-level column or value (file, line, function, frame, path, message text, input)
  - tools.csv has one row per tool; every system names a tool of it and carries a version, a configuration label and
    hash, a submission date, a first release, a status (current, superseded or withdrawn) and, where it replaces an
    entry, the system it supersedes; two systems never share all of tool, version, model, configuration, track and
    budget; a value that is not established is the word "unknown" and is counted, never guessed
  - RELEASE.json agrees with the tables, names the release (YYYY.N) and the fields suite, specification, programs tag,
    image, runner and previous release (null where not recorded), and carries the release flag
    "public"; every script starts with the SPDX identifier and the copyright line; the README keeps its line on what
    is and is not published about bugs

Usage:  python scripts/check.py
"""
from __future__ import annotations

import csv
import datetime
import gzip
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CONTRACT_TOTALS = {
    "bc": 1922, "confetti": 632, "diff": 4369, "du": 7331, "gawk": 20356, "gcal": 15632, "grep": 4806,
    "gzip": 3083, "jq": 18280, "krep": 1231, "libmatheval": 591, "libyaml": 4275, "ls": 4441, "oggenc": 1138,
    "sed": 6958, "woff2": 11412, "xmlwf": 6293, "xorriso": 54607,
}
UNIVERSE_EXCEPTIONS: dict = {}  # (measure, program) -> documented total; empty in release 0.1
KEY_EXCEPTIONS: dict = {}       # (system_id, program) -> covered keys documented as outside the universe; empty
NO_UNIVERSE: dict = {}          # program -> reason it has no gcovr-format source for a universe file; empty
# (system_id, program, repeat) -> the largest fall between two checkpoints of a curve that exceeds 10 branches. Each
# checkpoint is replayed separately, so a curve can fall between two of them (README, Curve dips). The standard replay
# of this release has no falling curve, so no exception is listed.
DIP_EXCEPTIONS: dict = {}
MEASURES = ["canonical", "standard", "common-observer", "published"]
FAMILIES = {"symbolic", "fuzzer", "hybrid", "llm", "agent", "other"}
TRACKS = {"open", "fixed"}
GROUPS = {"main", "ablation", "seeding", "control"}
CHECKPOINT_STEP = 900
STANDARD_BUDGET_S = 21600
STATUSES = {"current", "superseded", "withdrawn"}
UNKNOWN = "unknown"
RELEASE_RE = re.compile(r"^(20[0-9]{2})\.([1-9][0-9]*)$")     # a release is named YYYY.N
TOOL_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# the fields of RELEASE.json that say what the release was measured with; each is null or a non-empty string
RELEASE_RECORDED = ["suite", "specification", "programs_tag", "image", "runner"]
HEADERS = {
    "programs.csv": ["program", "name", "version", "branch_total", "inputs", "source_url", "fixed_track"],
    "systems.csv": ["system_id", "label", "family", "backend", "model", "cores", "budget_s", "track", "measure",
                    "group", "description", "reference", "tool_id", "version", "config", "config_sha256", "submitted",
                    "first_release", "status", "supersedes"],
    "tools.csv": ["tool_id", "name", "url", "reference", "description"],
    "runs.csv": ["system_id", "program", "repeat", "covered", "total", "measure", "set_path", "curve_path",
                 "output_tokens", "notes"],
    "derived/finals.csv": ["system_id", "program", "n_runs", "median", "min", "max", "total", "median_pct"],
    "derived/overlap.csv": ["program", "system_a", "system_b", "a_only", "b_only", "both"],
    "derived/signatures.csv": ["system_id", "program", "sanitizer", "kind", "signatures", "runs"],
    "derived/measure_agreement.csv": ["system_id", "program", "repeat", "measure_used", "measure_other",
                                      "only_used", "only_other", "note"],
}
REQUIRED = ["README.md", "LICENSE-DATA.md", "LICENSE", "RELEASE.json", "scripts/derive.py",
            "scripts/check.py"] + list(HEADERS)
SYSTEM_HEADERS = "/usr/include/"  # inline functions of system headers are part of the pinned universe
KEY_RE = re.compile(r"^[^:\s][^:\s]*:[0-9]+:[0-9]+:[0-9]+$")
KIND_RE = re.compile(r"^[A-Za-z][A-Za-z -]*$")  # an error class: letters, spaces and hyphens only
# The error classes that derived/signatures.csv may name (the sanitiser's own class, without types, sizes, values or a
# location). Nothing more specific is published: a new class is added here on purpose, never by a derive step alone.
ERROR_CLASSES = {
    "none",
    # AddressSanitizer, and the SEGV of either build
    "heap-buffer-overflow", "SEGV", "double-free", "heap-use-after-free", "strncpy-param-overlap", "strcpy-param-overlap",
    "global-buffer-overflow", "unknown-crash", "unreachable", "calloc-overflow", "stack-buffer-overflow", "stack-use-after-return",
    "use-after-poison", "memcpy-param-overlap", "negative-size-param", "alloc-dealloc-mismatch", "heap-buffer-underflow",
    "stack-overflow",
    # UndefinedBehaviorSanitizer
    "left shift of a negative value", "left shift out of range", "value outside the range of its type", "misaligned load",
    "misaligned store", "misaligned member access", "member access through a null pointer",
    "zero offset applied to a null pointer", "non-zero offset applied to a null pointer", "load through a null pointer",
    "null pointer passed as a non-null argument", "shift exponent too large", "signed integer overflow",
    "array index out of bounds",
    # The standard replay reports a coarser class than the earlier sanitiser replay for these four, and their own words
    # are the published class: one kind where the earlier table split misaligned loads, stores and member access; one
    # where it split pointer offsets; one where it split uses of a null pointer; and one where it split param-overlap
    # by the copying function. Nothing more specific than the class is published.
    "misaligned access", "pointer overflow", "null pointer use", "parameter overlap",
    # Every other kind the replay tool can report
    "division by zero", "unsigned integer overflow", "negation overflow", "implicit conversion", "invalid value load",
    "invalid builtin", "insufficient object space", "function type mismatch", "missing return",
    "null pointer returned as a non-null value", "variable-length array bound", "bad downcast", "bad vptr",
    "abrt", "allocation-size-too-big", "bad-free", "bad-malloc-usable-size", "bus", "container-overflow", "deadlysignal",
    "dynamic-stack-buffer-overflow", "failed-to-allocate", "fpe", "ill", "invalid-aligned-alloc-alignment",
    "invalid-allocation-alignment", "invalid-pointer-pair", "invalid-posix-memalign-alignment",
    "new-delete-type-mismatch", "odr-violation", "other", "out-of-memory", "pvalloc-overflow", "reallocarray-overflow",
    "stack-buffer-underflow", "stack-use-after-scope", "trap",
}

errors: list[str] = []


def err(msg):
    if len(errors) < 200:
        errors.append(msg)


def read_csv(name):
    path = ROOT / name
    with open(path, newline="") as fh:
        rows = list(csv.reader(fh))
    return rows[0], [dict(zip(rows[0], r)) for r in rows[1:]], rows[1:]


def load_set(path):
    with gzip.open(ROOT / path, "rb") as fh:
        return json.loads(fh.read())


def spec_fixed_track(ft):
    """The fixed_track column that a program definition of the specification repository gives."""
    if ft is None:
        return ""
    if ft["input_channel"] == "stdin":
        return "stdin" if not ft["argv_template"] else " ".join(ft["argv_template"]) + " < stdin"
    return " ".join(ft["argv_template"])


def bad_key(k):
    """Why `k` is not a well-formed branch key of this release, or None."""
    if not KEY_RE.match(k) or ".." in k.split(":")[0].split("/"):
        return "malformed key"
    if k.startswith("/") and not k.startswith(SYSTEM_HEADERS):
        return "an absolute path other than a system header"
    return None


def release_key(name):
    m = RELEASE_RE.match(name)
    return (int(m.group(1)), int(m.group(2)))


def plain(text):
    """True for a non-empty string without leading or trailing space and without control characters."""
    return isinstance(text, str) and text != "" and text == text.strip() and not any(ord(c) < 32 for c in text)


def check_identity(systems, tools, rel):
    """Release name, tools and the identity columns of systems.csv. Returns the number of values that are "unknown"."""
    name = rel.get("release")
    prev = rel.get("previous_release", "missing")
    if not isinstance(name, str) or not RELEASE_RE.match(name):
        err(f"RELEASE.json: release {name!r} is not a name of the form YYYY.N")
        name = None
    if prev == "missing" or not (prev is None or (isinstance(prev, str) and RELEASE_RE.match(prev))):
        err(f"RELEASE.json: previous_release {prev!r} must be null or a release name")
    elif prev is not None and name and release_key(prev) >= release_key(name):
        err(f"RELEASE.json: previous_release {prev} is not earlier than release {name}")
    for field in RELEASE_RECORDED:
        if field not in rel:
            err(f"RELEASE.json: {field} is missing (null if not recorded)")
        elif rel[field] is not None and not plain(rel[field]):
            err(f"RELEASE.json: {field} must be null or a non-empty string")

    # tools
    tool_ids = [t["tool_id"] for t in tools]
    if len(set(tool_ids)) != len(tool_ids):
        err("tools.csv: duplicate tool_id")
    for t in tools:
        if not TOOL_ID_RE.match(t["tool_id"]):
            err(f"tools.csv: odd tool_id {t['tool_id']!r}")
        for col in ("name", "description"):
            if not plain(t[col]):
                err(f"tools.csv: {t['tool_id']} has an empty or untrimmed {col}")
        for col in ("url", "reference"):
            if t[col] and not t[col].startswith("https://"):
                err(f"tools.csv: {t['tool_id']} {col} {t[col]!r} is neither empty nor an https address")
    used = {s["tool_id"] for s in systems}
    for tid in tool_ids:
        if tid not in used:
            err(f"tools.csv: {tid} has no system")

    # systems
    by_id = {s["system_id"]: s for s in systems}
    unknown = 0
    seen_identity = {}
    pointed = Counter()
    for s in systems:
        sid = s["system_id"]
        if s["tool_id"] not in tool_ids:
            err(f"systems.csv: {sid} names the tool {s['tool_id']!r}, which tools.csv does not list")
        for col in ("version", "config"):
            if not plain(s[col]) or len(s[col]) > 300:
                err(f"systems.csv: {sid} {col} must be a trimmed non-empty text of at most 300 characters")
            unknown += s[col] == UNKNOWN
        if s["config_sha256"] not in ("", UNKNOWN) and not SHA256_RE.match(s["config_sha256"]):
            err(f"systems.csv: {sid} config_sha256 {s['config_sha256']!r} is not empty, 'unknown' or 64 lowercase hex digits")
        unknown += s["config_sha256"] == UNKNOWN
        try:
            if len(s["submitted"]) != 10:
                raise ValueError
            datetime.date.fromisoformat(s["submitted"])
        except ValueError:
            err(f"systems.csv: {sid} submitted {s['submitted']!r} is not a date YYYY-MM-DD")
        if not RELEASE_RE.match(s["first_release"]):
            err(f"systems.csv: {sid} first_release {s['first_release']!r} is not a release name")
        elif name:
            if release_key(s["first_release"]) > release_key(name):
                err(f"systems.csv: {sid} first_release {s['first_release']} is later than release {name}")
            if prev is None and s["first_release"] != name:
                err(f"systems.csv: {sid} first_release {s['first_release']} but {name} is the first release")
        if s["status"] not in STATUSES:
            err(f"systems.csv: {sid} status {s['status']!r} is not one of {sorted(STATUSES)}")
        if s["supersedes"]:
            target = by_id.get(s["supersedes"])
            if target is None:
                err(f"systems.csv: {sid} supersedes {s['supersedes']!r}, which is not a system")
            elif target is s:
                err(f"systems.csv: {sid} supersedes itself")
            else:
                pointed[s["supersedes"]] += 1
                if target["tool_id"] != s["tool_id"]:
                    err(f"systems.csv: {sid} supersedes {s['supersedes']}, which is another tool")
                if target["status"] not in ("superseded", "withdrawn"):
                    err(f"systems.csv: {s['supersedes']} is superseded by {sid} but has status {target['status']!r}")
        key = (s["tool_id"], s["version"], s["model"], s["config"], s["config_sha256"], s["track"], s["cores"],
               s["budget_s"])
        if key in seen_identity:
            err(f"systems.csv: {sid} and {seen_identity[key]} agree in tool, version, model, configuration, track and "
                f"budget, so they are one entry")
        seen_identity[key] = sid
    for sid, n in pointed.items():
        if n > 1:
            err(f"systems.csv: {sid} is superseded by {n} systems")
    for s in systems:
        if s["status"] == "superseded" and not pointed[s["system_id"]]:
            err(f"systems.csv: {s['system_id']} has status 'superseded' but no system supersedes it")
        chain, cur = set(), s
        while cur["supersedes"] and cur["supersedes"] in by_id:
            if cur["system_id"] in chain:
                err(f"systems.csv: the supersedes chain of {s['system_id']} loops")
                break
            chain.add(cur["system_id"])
            cur = by_id[cur["supersedes"]]
    return unknown


def main():
    # ---- files and headers --------------------------------------------------------------------------
    for name in REQUIRED:
        if not (ROOT / name).is_file():
            err(f"missing file: {name}")
    if errors:
        return finish()
    tables = {}
    for name, header in HEADERS.items():
        h, rows, raw = read_csv(name)
        if h != header:
            err(f"{name}: header {h} != {header}")
        for i, r in enumerate(raw, 2):
            if len(r) != len(header):
                err(f"{name} line {i}: {len(r)} fields")
        tables[name] = rows
    if errors:  # a table with other columns than the specification cannot be read by the checks below
        return finish()
    programs, systems, runs = tables["programs.csv"], tables["systems.csv"], tables["runs.csv"]

    # ---- programs ---------------------------------------------------------------------------------
    ptotal = {}
    for p in programs:
        ptotal[p["program"]] = int(p["branch_total"])
        if not p["source_url"].startswith("https://"):
            err(f"programs.csv: {p['program']} source_url")
        if not p["version"] or not p["name"] or not p["inputs"]:
            err(f"programs.csv: {p['program']} has an empty field")
    if ptotal != CONTRACT_TOTALS:
        err(f"programs.csv totals differ from the specification: {set(ptotal.items()) ^ set(CONTRACT_TOTALS.items())}")
    porder = {p["program"]: i for i, p in enumerate(programs)}

    # ---- programs against the specification repository (when it is checked out next to this one) ---------
    spec_dir = next((d / "programs" for d in (ROOT.parent / "agent-software-testing",)
                     if (d / "programs").is_dir()), None)
    if spec_dir is None:
        print("specification repository not found next to this one; programs.csv not cross-checked against it")
    else:
        n_spec = 0
        for p in programs:
            f = spec_dir / p["program"] / "program.json"
            if not f.is_file():
                err(f"programs.csv: {p['program']} has no program definition in the specification repository")
                continue
            spec = json.loads(f.read_text())
            n_spec += 1
            want_row = {"version": spec["version"], "branch_total": str(spec["branch_total"]),
                        "inputs": ";".join(spec["inputs"]), "source_url": spec["source"]["url"],
                        "fixed_track": spec_fixed_track(spec["fixed_track"])}
            for col, want_val in want_row.items():
                if p[col] != want_val:
                    err(f"programs.csv: {p['program']} {col} is {p[col]!r}, the specification repository says "
                        f"{want_val!r}")
        print(f"programs.csv cross-checked against {n_spec} program definitions of the specification repository")

    # ---- universes ---------------------------------------------------------------------------------
    universe = {}
    for prog in ptotal:
        path = ROOT / "universe" / f"{prog}.txt.gz"
        if not path.is_file():
            if prog not in NO_UNIVERSE:
                err(f"universe/{prog}.txt.gz: missing")
            continue
        with gzip.open(path, "rb") as fh:
            raw = fh.read().decode()
        keys = raw.split("\n")
        if keys[-1] != "":
            err(f"universe/{prog}.txt.gz: does not end with a newline")
        keys = keys[:-1]
        if keys != sorted(keys) or len(set(keys)) != len(keys):
            err(f"universe/{prog}.txt.gz: keys not sorted and unique")
        if len(keys) != ptotal[prog]:
            err(f"universe/{prog}.txt.gz: {len(keys)} keys, programs.csv says {ptotal[prog]}")
        for k in keys:
            why = bad_key(k)
            if why:
                err(f"universe/{prog}.txt.gz: {why}: {k!r}")
                break
        universe[prog] = set(keys)
    for f in sorted((ROOT / "universe").glob("*")) if (ROOT / "universe").is_dir() else []:
        if f.name != f.name.lower() or not f.name.endswith(".txt.gz") or f.name[:-7] not in ptotal:
            err(f"universe/{f.name}: not the universe of a program of programs.csv")
    for prog, why in NO_UNIVERSE.items():
        print(f"no universe file for {prog}: {why}")

    # ---- systems ---------------------------------------------------------------------------------
    sids = [s["system_id"] for s in systems]
    if len(set(sids)) != len(sids):
        err("systems.csv: duplicate system_id")
    sorder = {s: i for i, s in enumerate(sids)}
    for s in systems:
        if s["family"] not in FAMILIES:
            err(f"systems.csv: {s['system_id']} family {s['family']}")
        if s["track"] not in TRACKS:
            err(f"systems.csv: {s['system_id']} track {s['track']}")
        if s["group"] not in GROUPS:
            err(f"systems.csv: {s['system_id']} group {s['group']}")
        for col in ("cores", "budget_s"):
            if s[col] and not s[col].isdigit():
                err(f"systems.csv: {s['system_id']} {col} {s[col]}")
        for col in ("label", "backend", "description", "reference"):
            if not s[col]:
                err(f"systems.csv: {s['system_id']} empty {col}")
        if not re.match(r"^[a-z0-9][a-z0-9.+_-]*$", s["system_id"]):
            err(f"systems.csv: odd system_id {s['system_id']}")
    rel = json.loads((ROOT / "RELEASE.json").read_text())
    n_unknown = check_identity(systems, tables["tools.csv"], rel)
    print(f"identity: {len(tables['tools.csv'])} tools, {len(systems)} systems, release {rel.get('release')}; "
          f"{n_unknown} identity values are 'unknown' (an unestablished value is never guessed)")

    # ---- runs ------------------------------------------------------------------------------------
    seen = set()
    per_pair = Counter()
    by_system_measures = defaultdict(set)
    referenced = set()
    for r in runs:
        if not all(r[col].isdigit() for col in ("repeat", "covered", "total")):
            err(f"runs.csv: {r['system_id']} on {r['program']}: repeat, covered and total must be whole numbers")
            continue
        sid, prog, rep = r["system_id"], r["program"], int(r["repeat"])
        key = (sid, prog, rep)
        if key in seen:
            err(f"runs.csv: duplicate run {key}")
        seen.add(key)
        per_pair[(sid, prog)] += 1
        if sid not in sorder:
            err(f"runs.csv: unknown system {sid}")
        if prog not in ptotal:
            err(f"runs.csv: unknown program {prog}")
            continue
        if r["measure"] not in MEASURES:
            err(f"runs.csv: {key} measure {r['measure']}")
        by_system_measures[sid].add(r["measure"])
        cov, tot = int(r["covered"]), int(r["total"])
        expected_total = UNIVERSE_EXCEPTIONS.get((r["measure"], prog), ptotal[prog])
        if tot != expected_total:
            err(f"runs.csv: {key} total {tot} != {expected_total}")
        if not 0 <= cov <= tot:
            err(f"runs.csv: {key} covered {cov} outside 0..{tot}")
        if r["output_tokens"] and not r["output_tokens"].isdigit():
            err(f"runs.csv: {key} output_tokens {r['output_tokens']}")
        if r["set_path"]:
            if r["set_path"] != f"sets/{sid}/{prog}/r{rep}.json.gz":
                err(f"runs.csv: {key} set_path {r['set_path']}")
            referenced.add(r["set_path"])
        elif "no branch set held" not in r["notes"]:
            err(f"runs.csv: {key} has no set_path and no note saying so")
        if (r["measure"] == "published") != (not r["set_path"]):
            err(f"runs.csv: {key} measure {r['measure']!r} but set_path {r['set_path']!r}: a run without a "
                f"branch set is a published count, and only those")
        if r["curve_path"]:
            if r["curve_path"] != f"curves/{sid}/{prog}/r{rep}.csv":
                err(f"runs.csv: {key} curve_path {r['curve_path']}")
            referenced.add(r["curve_path"])
    # Those runs are reported above; the checks below look every run up by system and program and need whole numbers.
    runs = [r for r in runs if r["system_id"] in sorder and r["program"] in ptotal
            and all(r[col].isdigit() for col in ("repeat", "covered", "total"))]
    # A run with fewer than two companions on its program is not marked: a replay that has not finished yet leaves runs
    # out of the release, and the release holds one measurement only, so its rows carry no note of their own. Overlap
    # simply admits a pair only when repeats 1, 2 and 3 are all present (see below).
    for s in systems:
        want = "+".join(m for m in MEASURES if m in by_system_measures.get(s["system_id"], set()))
        if not want:
            err(f"systems.csv: {s['system_id']} has no runs")
        elif s["measure"] != want:
            err(f"systems.csv: {s['system_id']} measure {s['measure']} != {want}")
    order = [(sorder[r["system_id"]], porder[r["program"]], int(r["repeat"])) for r in runs]
    if order != sorted(order):
        err("runs.csv is not sorted by system, program, repeat")

    # ---- sets ------------------------------------------------------------------------------------
    n_sets = n_keys = n_outside = 0
    for r in runs:
        if not r["set_path"]:
            continue
        sid, prog, rep = r["system_id"], r["program"], int(r["repeat"])
        path = ROOT / r["set_path"]
        if not path.is_file():
            err(f"{r['set_path']}: missing")
            continue
        d = load_set(r["set_path"])
        n_sets += 1
        if list(d) != ["schema", "program", "branch_key_format", "branch_total", "branch_covered",
                       "covered_branch_keys"]:
            err(f"{r['set_path']}: fields {list(d)}")
            continue
        if d["schema"] != "agent-software-testing/branch-set@0.1" or d["branch_key_format"] != "file:line:block_id:branch_id":
            err(f"{r['set_path']}: schema or key format")
        if d["program"] != prog:
            err(f"{r['set_path']}: program {d['program']}")
        keys = d["covered_branch_keys"]
        n_keys += len(keys)
        if keys != sorted(keys) or len(set(keys)) != len(keys):
            err(f"{r['set_path']}: keys not sorted and unique")
        bad = next((k for k in keys if bad_key(k)), None)
        if bad is not None:
            err(f"{r['set_path']}: {bad_key(bad)}: {bad!r}")
        if d["branch_covered"] != len(keys) or d["branch_covered"] != int(r["covered"]):
            err(f"{r['set_path']}: covered {d['branch_covered']}/{len(keys)} vs runs.csv {r['covered']}")
        if d["branch_total"] != int(r["total"]):
            err(f"{r['set_path']}: total {d['branch_total']} vs runs.csv {r['total']}")
        if d["branch_total"] != UNIVERSE_EXCEPTIONS.get((r["measure"], prog), ptotal[prog]):
            err(f"{r['set_path']}: total {d['branch_total']} vs programs.csv {ptotal[prog]}")
        if len(keys) > d["branch_total"]:
            err(f"{r['set_path']}: more keys than branches")
        if prog in universe:
            outside = [k for k in keys if k not in universe[prog]]
            allowed = KEY_EXCEPTIONS.get((sid, prog), set())
            if outside and not set(outside) <= set(allowed):
                err(f"{r['set_path']}: {len(outside)} covered keys are not in the universe of {prog}, "
                    f"e.g. {outside[0]!r}")
            n_outside += len(outside)

    # ---- curves ----------------------------------------------------------------------------------
    n_curves = 0
    budget_of = {s["system_id"]: s["budget_s"] for s in systems}
    for r in runs:
        if not r["curve_path"]:
            continue
        path = ROOT / r["curve_path"]
        if not path.is_file():
            err(f"{r['curve_path']}: missing")
            continue
        n_curves += 1
        with open(path, newline="") as fh:
            rows = list(csv.reader(fh))
        if rows[0] != ["t_s", "covered"]:
            err(f"{r['curve_path']}: header")
            continue
        try:
            t = [int(x[0]) for x in rows[1:]]
            c = [int(x[1]) for x in rows[1:]]
        except ValueError:
            err(f"{r['curve_path']}: non-integer value")
            continue
        budget = budget_of.get(r["system_id"], "")
        budget = int(budget) if budget.isdigit() else STANDARD_BUDGET_S
        if t != list(range(0, budget, CHECKPOINT_STEP)) + [budget]:
            err(f"{r['curve_path']}: checkpoints do not follow the budget of {budget} s")
        notes = [n.strip() for n in r["notes"].split(";")]
        dip = max(a - b for a, b in zip(c, c[1:]))
        sid_, prog_, rep_ = r["system_id"], r["program"], int(r["repeat"])
        stated_dip = [int(m.group(1)) for n in notes for m in [re.match(r"curve dips by up to (\d+) ", n)] if m]
        if (dip > 0) != bool(stated_dip) or (stated_dip and stated_dip != [dip]):
            err(f"{r['curve_path']}: dips by {dip} but notes say {stated_dip}")
        if dip > DIP_EXCEPTIONS.get((sid_, prog_, rep_), 10):
            err(f"{r['curve_path']}: dips by {dip}")
        if (sid_, prog_, rep_) in DIP_EXCEPTIONS and dip != DIP_EXCEPTIONS[(sid_, prog_, rep_)]:
            err(f"{r['curve_path']}: dips by {dip}, DIP_EXCEPTIONS says {DIP_EXCEPTIONS[(sid_, prog_, rep_)]}")
        if c[0] < 0 or c[-1] > int(r["total"]) + 4:
            err(f"{r['curve_path']}: values out of range")
        stated = [int(n[len("curve ends at "):]) for n in notes if n.startswith("curve ends at ")]
        if c[-1] != int(r["covered"]) and stated != [c[-1]]:
            err(f"{r['curve_path']}: ends at {c[-1]}, covered {r['covered']}, notes {r['notes']!r}")
        if c[-1] == int(r["covered"]) and stated:
            err(f"{r['curve_path']}: note says the curve ends elsewhere but it ends at covered")

    # ---- no orphans ------------------------------------------------------------------------------
    for top in ("sets", "curves"):
        for p in (ROOT / top).rglob("*"):
            if p.is_file() and p.relative_to(ROOT).as_posix() not in referenced:
                err(f"unreferenced file: {p.relative_to(ROOT)}")

    # ---- derived: finals -------------------------------------------------------------------------
    group = defaultdict(list)
    for r in runs:
        group[(r["system_id"], r["program"])].append(int(r["covered"]))
    want = []
    for (sid, prog), cov in sorted(group.items(), key=lambda kv: (sorder[kv[0][0]], porder[kv[0][1]])):
        med = statistics.median(cov)
        want.append([sid, prog, str(len(cov)), str(int(med)) if float(med).is_integer() else f"{med:.1f}",
                     str(min(cov)), str(max(cov)), str(ptotal[prog]), f"{100.0 * med / ptotal[prog]:.2f}"])
    _, _, got = read_csv("derived/finals.csv")
    if got != want:
        err(f"derived/finals.csv differs from the recomputation ({len(got)} rows, expected {len(want)})")

    # ---- derived: overlap ------------------------------------------------------------------------
    incomplete_pairs = {(r["system_id"], r["program"]) for r in runs if "incomplete" in r["notes"]}
    sets_by_pair = defaultdict(dict)
    for r in runs:
        if r["set_path"] and int(r["repeat"]) in (1, 2, 3):
            sets_by_pair[(r["system_id"], r["program"])][int(r["repeat"])] = r["set_path"]
    union = {}
    for (sid, prog), reps in sets_by_pair.items():
        if sorted(reps) == [1, 2, 3] and (sid, prog) not in incomplete_pairs:
            u = set()
            for p in reps.values():
                u.update(load_set(p)["covered_branch_keys"])
            union[(sid, prog)] = u
    expected_pairs = set()
    for prog in ptotal:
        have = sorted((s for (s, p) in union if p == prog), key=lambda s: sorder[s])
        for a, b in combinations(have, 2):
            expected_pairs.add((prog, a, b))
    seen_pairs = set()
    for row in tables["derived/overlap.csv"]:
        prog, a, b = row["program"], row["system_a"], row["system_b"]
        k = (prog, a, b)
        if k in seen_pairs:
            err(f"overlap.csv: duplicate {k}")
        seen_pairs.add(k)
        if k not in expected_pairs:
            err(f"overlap.csv: pair not admitted by the rule {k}")
            continue
        ua, ub = union[(a, prog)], union[(b, prog)]
        both = len(ua & ub)
        if (int(row["a_only"]), int(row["b_only"]), int(row["both"])) != (len(ua) - both, len(ub) - both, both):
            err(f"overlap.csv: wrong counts for {k}")
    if seen_pairs != expected_pairs:
        err(f"overlap.csv: {len(expected_pairs - seen_pairs)} admitted pairs missing")
    overlap_rows = [(r["program"], porder[r["program"]]) for r in tables["derived/overlap.csv"]]
    if [x[1] for x in overlap_rows] != sorted(x[1] for x in overlap_rows):
        err("overlap.csv: not ordered by program")

    # ---- derived: signatures ---------------------------------------------------------------------
    sig_seen = set()
    groups = defaultdict(list)
    with open(ROOT / "derived/signatures.csv") as fh:
        text = fh.read()
    if re.search(r"\.(c|h|cc|cpp|y|l)\b|@|/|0x[0-9a-fA-F]|#[0-9]|:[0-9]|\(|\)", text):
        err("derived/signatures.csv: looks like it contains a file name, line, function, frame or address")
    for r in tables["derived/signatures.csv"]:
        k = (r["system_id"], r["program"], r["sanitizer"], r["kind"])
        if k in sig_seen:
            err(f"signatures.csv: duplicate {k}")
        sig_seen.add(k)
        groups[k[:3]].append(r)
        if r["system_id"] not in sorder or r["program"] not in ptotal:
            err(f"signatures.csv: unknown system or program {k}")
        if r["sanitizer"] not in ("ASan", "UBSan"):
            err(f"signatures.csv: sanitizer {r['sanitizer']}")
        if not KIND_RE.match(r["kind"]):
            err(f"signatures.csv: kind {r['kind']!r} is not an error class")
        elif r["kind"] not in ERROR_CLASSES:
            err(f"signatures.csv: kind {r['kind']!r} is not one of the published error classes (nothing more specific than the class is published)")
        n, nr = int(r["signatures"]), int(r["runs"])
        if n < 0 or (r["kind"] == "none" and n != 0) or (r["kind"] != "none" and n < 1):
            err(f"signatures.csv: count {n} for {k}")
        if not 1 <= nr <= per_pair.get((r["system_id"], r["program"]), 0):
            err(f"signatures.csv: runs {nr} for {k} but runs.csv has {per_pair.get((r['system_id'], r['program']), 0)}")
    for g, rs in groups.items():
        if any(r["kind"] == "none" for r in rs) and len(rs) > 1:
            err(f"signatures.csv: 'none' next to other kinds for {g}")
        if len({r["runs"] for r in rs}) > 1:
            err(f"signatures.csv: runs differ within {g}")
    for (sid, prog) in {(k[0], k[1]) for k in groups}:
        for san in ("ASan", "UBSan"):
            if (sid, prog, san) not in groups:
                err(f"signatures.csv: {sid} {prog} has no row for {san}")

    # ---- derived: measure agreement ----------------------------------------------------------------
    documented = {("agolic-klee-gpt55", "oggenc", "canonical"), ("agolic-aflpp-deepseek", "oggenc", "common-observer")}
    for r in tables["derived/measure_agreement.csv"]:
        if (int(r["only_used"]) or int(r["only_other"])) and (r["system_id"], r["program"], r["measure_other"]) not in documented:
            err(f"measure_agreement.csv: undocumented difference {r}")

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import derive  # noqa: E402
    readme = (ROOT / "README.md").read_text()

    # ---- README: systems table is current ---------------------------------------------------------
    if derive.README_START not in readme or derive.README_END not in readme:
        err("README.md: systems markers missing")
    else:
        between = readme.split(derive.README_START, 1)[1].split(derive.README_END, 1)[0].strip("\n")
        if between != derive.systems_table(runs, systems):
            err("README.md: systems table is out of date (run scripts/derive.py)")

    # ---- RELEASE.json ----------------------------------------------------------------------------
    counts = rel.get("counts", {})
    want_counts = {"systems": len(systems), "programs": len(programs), "universes": len(universe),
                   "runs": len(runs), "runs_with_branch_sets": n_sets, "runs_with_curves": n_curves}
    if counts != want_counts:
        err(f"RELEASE.json counts {counts} != {want_counts}")
    # The shape of the two fields, not a release's literal values: a new release bumps them and must pass.
    try:
        datetime.date.fromisoformat(rel.get("snapshot_date", ""))
    except (TypeError, ValueError):
        err("RELEASE.json: snapshot_date is not an ISO date (YYYY-MM-DD)")
    if not isinstance(rel.get("version"), str) or not re.fullmatch(r"\d+\.\d+\.\d+", rel["version"]):
        err("RELEASE.json: version is not of the form N.N.N")
    try:
        if not isinstance(rel.get("date"), str):
            raise ValueError
        datetime.date.fromisoformat(rel["date"])
    except ValueError:
        err("RELEASE.json: date is not an ISO date (YYYY-MM-DD)")
    if not isinstance(rel.get("public"), bool):
        err('RELEASE.json: the release flag "public" is missing or not true/false')
    elif not rel["public"]:
        print("release flag: public = false (private repository)")

    # ---- submission manifests: the structure of submissions/schema.json, checked with the standard library ---------
    import validate_manifest  # noqa: E402  (scripts/ is on the path when this script is run)
    schema = json.loads((ROOT / "submissions" / "schema.json").read_text())
    for path in sorted((ROOT / "submissions").glob("*.json")):
        if path.name == "schema.json":
            continue
        for problem in validate_manifest.validate_file(path, schema):
            err(f"submissions/{path.name}: {problem}")

    # ---- scripts carry the licence header; the README keeps the disclosure line -----------------------------
    for f in sorted((ROOT / "scripts").glob("*.py")):
        head = f.read_text().splitlines()[:3]
        if head[1:3] != ["# SPDX-License-Identifier: Apache-2.0", "# Copyright 2026 The Agent Software Testing authors"]:
            err(f"scripts/{f.name}: missing SPDX identifier and copyright line after the shebang")
    flat_readme = " ".join(readme.split())
    if "Kinds and counts are published for every program." not in flat_readme or \
            "Locations, stack traces and inputs of bugs not yet fixed upstream are not published" not in flat_readme:
        err("README.md: the lines on what is published about sanitiser findings (kinds and counts for every program; "
            "no locations, stack traces or inputs of unfixed bugs) are missing")
    if "withheld until the fixes are public" in flat_readme:
        err("README.md: still says that error classes are withheld")

    print(f"checked {len(systems)} systems, {len(programs)} programs, {len(universe)} universes "
          f"({sum(len(u) for u in universe.values()):,} keys), {len(runs)} runs, {n_sets} sets ({n_keys:,} keys, "
          f"{n_outside} outside their universe, {sum(len(v) for v in KEY_EXCEPTIONS.values())} documented "
          f"exceptions), {n_curves} curves, {len(tables['derived/overlap.csv'])} overlap rows, "
          f"{len(tables['derived/signatures.csv'])} signature rows")
    return finish()


def finish():
    if errors:
        print(f"{len(errors)} problem(s):")
        for e in errors:
            print("  " + e)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
