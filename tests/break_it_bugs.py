# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Agent Software Testing authors
"""The bugs tests/break_it.py makes on purpose.

Each row is (id, file, old text, new text, what the bug is). The id starts with the promise of tests/README.md that the
bug breaks. The old text must occur exactly once in the file. The bugs are the kind a person or an agent writes by
accident, chosen from what each promise protects and not from what the tests happen to check.
"""

C = "scripts/check.py"
D = "scripts/derive.py"
I = "scripts/import_replay.py"
A = "scripts/add_signatures.py"
V = "scripts/validate_manifest.py"
P = "scripts/package_inputs.py"
J = "scripts/replay_job.py"
U = "scripts/publish_submission.py"

# the test files that can notice a bug in a script (the others do not run it, directly or through another script)
TESTS_FOR = {
    U: ["tests/test_publish_submission.py"],
    C: ["tests/test_release_check.py", "tests/test_import_replay.py", "tests/test_derive.py"],
    D: ["tests/test_derive.py", "tests/test_release_check.py", "tests/test_import_replay.py"],
    I: ["tests/test_import_replay.py", "tests/test_add_signatures.py", "tests/test_release_check.py"],
    A: ["tests/test_add_signatures.py"],
    V: ["tests/test_validate_manifest.py", "tests/test_release_check.py"],
    P: ["tests/test_package_inputs.py"],
    J: ["tests/test_replay_job.py"],
}

BUGS = [
    ("U1-a", U, 'isinstance(r, dict) and r.get("conclusion") == "success"]', 'isinstance(r, dict)]', "a failed replay counts as passed"),
    ("U1-b", U, 'if plan.get("pr") == pr and plan.get("head_sha") == head_sha:', 'if plan.get("pr") == pr:', "a replay of another commit counts"),
    ("U1-c", U, 'if plan.get("pr") == pr and plan.get("head_sha") == head_sha:', 'if plan.get("head_sha") == head_sha:', "a replay of another pull request counts"),
    ("U1-d", U, 'if wanted != planned or plan.get("system_id") != stem:', 'if plan.get("system_id") != stem:', "the manifest may list other runs than were replayed"),
    ("U2-a", U, '    run_script(root, "check.py")\n    return flags', '    return flags', "the new tree is not checked"),
    ("U2-b", U, 'if len(repeats_of[program]) < 3:', 'if len(repeats_of[program]) < 1:', "too few repeats are not marked"),
    ("U2-c", U, 'release.update(date=today.isoformat(),', 'release.update(version="9.9.9", date=today.isoformat(),', "each update makes a new version"),
    ("U2-d", U, '"covered_branch_keys": keys}', '"covered_branch_keys": keys, **{k: v for k, v in coverage.items() if k == "extra"}}', "fields the data does not define get into a set"),
    ("U2-e", U, 'if best and median > FLAG_ABOVE_BEST * best:', 'if False:', "a number far above the best is not flagged"),
    ("U5-a", U, 'match[0]["status"] = "withdrawn"', 'pass', "withdrawing changes nothing"),
    ("U3-a", U, 'return rj.clean(value or "", limit) or rj.clean(fallback, limit)', 'return str(value or fallback)[:limit]', "notes reach the tables uncleaned"),
    ("U3-b", U, """parts = [f"{rj.clean(tool['name'], 80)} {rj.clean(tool['version'], 40)}"]""", """parts = [f"{tool['name']} {tool['version']}"]""", "a tool name reaches the label uncleaned"),
    ("U4-a", U, 'if named is None or named in {s["system_id"] for s in read_table(root / "systems.csv")[1]}:', 'if named is None:', "a change to a published manifest tries a new release"),
    ("U6-a", J, 'if every_file - set(candidates):', 'if False:', "a submission can carry code along"),
    ("U6-b", J, 'every_file.update(f["filename"] for f in files if isinstance(f, dict) and isinstance(f.get("filename"), str))', 'every_file.update(names)', "a file a pull request deletes or changes is not noticed"),
    ("U4-b", J, 'if stem in published_systems(root):', 'if False:', "a published name can be submitted again"),
    ('W-a', J, 'if not value.startswith("https://"):', 'if False:', 'a link that is not https is accepted'),
    ('W-b', J, 'if last.isdigit() or last.startswith("0x"):', 'if False:', 'an address written as one number passes as a host name'),
    ('W-c', J, 'all(ipaddress.ip_address(item[4][0]).is_global for item in found)', 'True', "a host name may lead to the runner's own network"),
    ('W-d', J, 'if name.startswith("/") or ".." in parts or "\\x00" in name or "\\\\" in name:', 'if False:', 'an archive may write outside its folder'),
    ('W-e', J, 'if digest.hexdigest() != expected:', 'if False:', 'a bundle whose digest differs from the manifest is replayed'),
    ('W-f', J, 'if size > max_bytes:', 'if False:', 'a download has no size limit'),
    ('W-g', J, 'if "setup_error_example" in doc:', 'if False:', 'the text of a failed case stays in the public record'),
    ('W-h', J, 'if sig.get("kind") not in add_signatures.KIND_SPELLING:', 'if False:', 'an unknown kind is not reported as such'),
    ('W-i', J, '"errors": [clean(e, 300) for e in errors[:8]]', '"errors": errors[:8]', 'a huge manifest error makes the report unreadable'),
    ('W-j', J, 'if re.fullmatch(r"[0-9a-f]{40}", commit):', 'if commit:', 'the comment prints what it was given as a commit'),
    ('W-k', J, '"--memory", "14g", "--memory-swap", "14g", ', '', 'the container has no memory bound'),
    ('W-l', J, '    if status >= 300:\n        raise OSError(f"GitHub refused the comment (status {status})")\n    return "created"', '    return "created"', 'a refused comment reads as a success'),
    ('W-m', J, 'parts = [f"{cov[key]} {label}" for key, label in (("timeouts", "timed out"), ("not_started", "did not start"))\n             if cov.get(key)]', 'parts = []', 'cases without a full measurement are not shown'),
    # R2  check.py refuses a release that is not consistent
    ("R2-a", C, '        if not (ROOT / name).is_file():\n            err(f"missing file: {name}")', "        if not (ROOT / name).is_file():\n            pass", "a check that passes when a required file is missing"),
    ("R2-b", C, "        if len(keys) != ptotal[prog]:", "        if False:", "a universe of the wrong size is accepted"),
    ("R2-c", C, "        keys = keys[:-1]\n        if keys != sorted(keys) or len(set(keys)) != len(keys):", "        keys = keys[:-1]\n        if False:", "a universe with keys out of order or twice is accepted"),
    ("R2-d", C, "            if outside and not set(outside) <= set(allowed):", "            if False:", "a covered branch that is not in the program is accepted"),
    ("R2-e", C, "        if keys != sorted(keys) or len(set(keys)) != len(keys):\n            err(f\"{r['set_path']}: keys not sorted and unique\")", "        if False:\n            err(f\"{r['set_path']}: keys not sorted and unique\")", "a set with keys out of order or twice is accepted"),
    ("R2-f", C, "        if d[\"branch_covered\"] != len(keys) or d[\"branch_covered\"] != int(r[\"covered\"]):", "        if d[\"branch_covered\"] != len(keys):", "a set is not compared with the covered count of its run"),
    ("R2-h", C, "            if p.is_file() and p.relative_to(ROOT).as_posix() not in referenced:", "            if False:", "files that no run names are accepted"),
    ("R2-i", C, "        if c[-1] != int(r[\"covered\"]) and stated != [c[-1]]:", "        if False:", "a curve may end away from its run"),
    ("R2-j", C, "        if t != list(range(0, budget, CHECKPOINT_STEP)) + [budget]:", "        if False:", "a curve may miss a checkpoint"),
    ("R2-k", C, "        if dip > DIP_EXCEPTIONS.get((sid_, prog_, rep_), 10):", "        if dip > DIP_EXCEPTIONS.get((sid_, prog_, rep_), 10000):", "a curve may fall by any amount"),
    ("R2-l", C, "    if got != want:", "    if False:", "finals.csv is not compared with a recomputation"),
    ("R2-m", C, "        if (int(row[\"a_only\"]), int(row[\"b_only\"]), int(row[\"both\"])) != (len(ua) - both, len(ub) - both, both):", "        if False:", "overlap counts are not recomputed"),
    ("R2-n", C, "    if seen_pairs != expected_pairs:", "    if False:", "overlap pairs may be missing"),
    ("R2-o", C, "    if re.search(r\"\\.(c|h|cc|cpp|y|l)\\b|@|/|0x[0-9a-fA-F]|#[0-9]|:[0-9]|\\(|\\)\", text):", "    if False:", "a file name or line in the signature table is accepted"),
    ("R2-q", C, "        elif r[\"kind\"] not in ERROR_CLASSES:", "        elif False:", "an unlisted error class is published"),
    ("R2-r", C, "    if counts != want_counts:", "    if False:", "the release record may state the wrong counts"),
    ("R2-s", C, "    if not isinstance(rel.get(\"public\"), bool):", "    if False:", "the public flag may be missing"),
    ("R2-t", C, "        if field not in rel:", "        if False:", "the record may forget what the release was measured with"),
    ("R2-u", C, "        if key in seen_identity:", "        if False:", "two systems that are one entry are accepted"),
    ("R2-v", C, "        if s[\"status\"] == \"superseded\" and not pointed[s[\"system_id\"]]:", "        if False:", "a system may be superseded by nothing"),
    ("R2-w", C, "        if key in seen:", "        if False:", "a run may be listed twice"),
    ("R2-x", C, "    if order != sorted(order):", "    if False:", "runs may be out of order"),
    ("R2-y", C, "        if tid not in used:", "        if False:", "a tool without a system is accepted"),
    ("R2-z", C, "        if between != derive.systems_table(runs, systems):", "        if False:", "an out-of-date systems table is accepted"),
    ("R2-A", C, "    if \"Kinds and counts are published for every program.\" not in flat_readme or \\\n            \"Locations, stack traces and inputs of bugs not yet fixed upstream are not published\" not in flat_readme:", "    if False:", "the line on what is not published may be dropped"),
    ("R2-B", C, "        if head[1:3] != [\"# SPDX-License-Identifier: Apache-2.0\", \"# Copyright 2026 The Agent Software Testing authors\"]:", "        if False:", "a script may lack its licence line"),
    ("R2-C", C, "        for problem in validate_manifest.validate_file(path, schema):", "        for problem in []:", "submission manifests are not checked"),
    ("R2-D", C, "    if errors:\n        print(f\"{len(errors)} problem(s):\")\n        for e in errors:\n            print(\"  \" + e)\n        return 1", "    if errors:\n        print(f\"{len(errors)} problem(s):\")\n        for e in errors:\n            print(\"  \" + e)\n        return 0", "problems are printed and the exit status is still success"),
    ("R2-F", C, "    if ptotal != CONTRACT_TOTALS:", "    if False:", "a changed branch total of a program is accepted"),
    ("R2-G", C, "        if tot != expected_total:", "        if False:", "a run may be counted on another universe"),
    ("R2-H", C, "        if d[\"program\"] != prog:", "        if False:", "a set of another program is accepted"),
    ("R2-I", C, "        if s[\"tool_id\"] not in tool_ids:", "        if False:", "a system may name a tool that is not defined"),
    ("R2-J", C, "        elif s[\"measure\"] != want:", "        elif False:", "the measure of a system need not agree with its runs"),

    # R1  a good release is not refused
    ("R1-a", C, "        if keys != sorted(keys) or len(set(keys)) != len(keys):\n            err(f\"universe/{prog}.txt.gz: keys not sorted and unique\")", "        if keys != sorted(keys, reverse=True) or len(set(keys)) != len(keys):\n            err(f\"universe/{prog}.txt.gz: keys not sorted and unique\")", "universe keys must be in descending order"),
    ("R1-b", C, "        if t != list(range(0, budget, CHECKPOINT_STEP)) + [budget]:", "        if t != list(range(0, budget + 1, CHECKPOINT_STEP)) + [budget]:", "an off-by-one in the checkpoints of a curve"),

    # T1  the tables people read are computed by one rule
    ("T1-a", D, "        med = statistics.median(cov)", "        med = statistics.mean(cov)", "the mean stands for the median"),
    ("T1-b", D, "        rows.append([sid, prog, len(cov), fmt(med), min(cov), max(cov), ptotal[prog],", "        rows.append([sid, prog, len(cov), fmt(med), max(cov), min(cov), ptotal[prog],", "minimum and maximum swapped"),
    ("T1-c", D, "        if \"incomplete\" in r[\"notes\"]:", "        if False:", "incomplete runs are compared"),
    ("T1-d", D, "if pr != prog or sorted(by_rep) != [1, 2, 3] or (sid, pr) in incomplete:", "if pr != prog or sorted(by_rep) != [1, 2] or (sid, pr) in incomplete:", "two repeats are enough for an overlap"),
    ("T1-e", D, "            for r in by_rep.values():\n                u.update(load_keys(r[\"set_path\"]))", "            for r in list(by_rep.values())[:1]:\n                u.update(load_keys(r[\"set_path\"]))", "the union covers one repeat only"),
    ("T1-f", D, "            rows.append([prog, a, b, len(unions[a]) - both, len(unions[b]) - both, both])", "            rows.append([prog, a, b, len(unions[b]) - both, len(unions[a]) - both, both])", "the two sides of an overlap are swapped"),
    ("T1-g", D, "        if totals != {ptotal[prog]}:\n            raise SystemExit", "        if False:\n            raise SystemExit", "runs on another universe enter the medians"),
    ("T2-a", D, '        lines.append("| `{}` | {} | {} | {} | {} | {} | {} |".format(', '        lines.append("| `{}` | {} | {} | {} | {} | {} | {}|".format(', "the README table is written with other spacing"),
    ("T1-h", D, "        if r[\"set_path\"] and int(r[\"repeat\"]) in (1, 2, 3):", "        if r[\"set_path\"] and int(r[\"repeat\"]) in (1, 2, 3, 4):", "the fourth repeat enters the overlap"),

    # I1-I4  import_replay.py
    ("I-a", I, "        if sha256_of(path) != digest:", "        if False:", "the replay's manifest is read but not compared"),
    ("I-b", I, "        if total != ptotal[key[1]]:", "        if False:", "a replay with another total than the program's is imported"),
    ("I-c", I, "    if any(a > b for a, b in zip(c, c[1:])):", "    if False:", "a falling curve is imported"),
    ("I-d", I, "    if c[-1] != covered:", "    if False:", "a curve that ends away from its run is imported"),
    ("I-e", I, "    if keys != sorted(keys) or len(set(keys)) != len(keys):\n        stop(", "    if False:\n        stop(", "a set with keys out of order is imported"),
    ("I-f", I, "        if doc[\"branch_covered\"] != covered:", "        if False:", "a set that disagrees with its coverage row is imported"),
    ("I-g", I, "        if r[\"state\"] == \"done\":", "        if r[\"state\"] in (\"done\", \"missing\"):", "runs that were not replayed enter"),
    ("I-h", I, "        if existing:\n            stop(f\"refusing to overwrite", "        if False:\n            stop(f\"refusing to overwrite", "an import writes over an existing release"),
    ("I-i", I, "    return m.group(1)\n", "    return value\n", "the image is named by the path it lived at"),
    ("I-j", I, "    if profile != PROFILE:", "    if False:", "a replay with another profile is imported"),
    ("I-k", I, "    if unknown:\n        stop(", "    if False:\n        stop(", "a replay of a system the release does not know is imported"),
    ("I-l", I, "    if resolved != ROOT and ROOT not in resolved.parents:", "    if False:", "the cleanup removes files outside the repository"),
    ("I-m", I, "with gzip.GzipFile(filename=\"\", mode=\"wb\", fileobj=fh, mtime=0, compresslevel=9) as gz:", "with gzip.GzipFile(filename=\"\", mode=\"wb\", fileobj=fh, compresslevel=9) as gz:", "the gzip header holds the time it was written"),
    ("I-n", I, "    missing = set(done) - set(cov)\n    extra = set(cov) - set(done)\n    if missing:", "    missing = set(done) - set(cov)\n    extra = set(cov) - set(done)\n    if False:", "a done run without a coverage row is imported"),
    ("I-o", I, "        if not path.is_file():\n        stop(f\"{path.relative_to(tables)}: the run is done but has no branch set\")".replace("        if", "    if"), "    if False:\n        stop(f\"{path.relative_to(tables)}: the run is done but has no branch set\")", "a done run without its set is imported"),

    # S1  add_signatures.py
    ("S-a", A, "            frames[san, kind].add(frame)", "            frames[san, kind].add(kind)", "signatures are counted by kind and not by kind and frame"),
    ("S-b", A, "    if any(r[\"system_id\"] == system and r[\"program\"] == program for r in existing):", "    if False:", "a system already in the table is added again"),
    ("S-c", A, "            if kind not in KIND_SPELLING:", "            if False and kind not in KIND_SPELLING:", "an unknown error kind is written as it is"),
    ("S-d", A, "        for san, kind, frame in found:", "        for san, kind, frame in list(found)[:1]:", "only one finding of a repeat is counted"),

    # V1  validate_manifest.py
    ("V-a", V, "            if key not in instance:", "            if False:", "a missing required field is accepted"),
    ("V-b", V, "            elif schema.get(\"additionalProperties\") is False:", "            elif False:", "an unexpected field is accepted"),
    ("V-c", V, "        if \"pattern\" in schema and re.search(schema[\"pattern\"], instance) is None:", "        if False:", "a pattern is not checked"),
    ("V-d", V, "    return 1 if failed else 0", "    return 0", "the command says success for an invalid manifest"),
    ("V-e", V, "        if \"enum\" in schema and instance not in schema[\"enum\"]:".replace("        if", "    if"), "    if False:", "an enum is not checked"),
    ("V-f", V, "        if \"minimum\" in schema and instance < schema[\"minimum\"]:", "        if \"minimum\" in schema and instance <= schema[\"minimum\"]:", "a number equal to the minimum is refused"),

    # K1  package_inputs.py
    ("K-a", P, 'RESTRICTED = ["bc", "diff", "gawk", "gcal", "jq", "oggenc", "xorriso"]', 'RESTRICTED = ["bc", "diff", "gawk", "gcal", "oggenc", "xorriso"]', "a program whose inputs are held back is packed"),
    ("K-b", P, "    restricted = {p.lower() for p in args.restricted}", "    restricted = set(args.restricted)", "restricted programs are matched by exact case"),
    ("K-c", P, "        if base in runs:", "        if False:", "two bundles of one run are packed"),
    ("K-d", P, "        if parts[-1] and used + cost(line, names - held) > limit:", "        if parts[-1] and used + cost(line, names - held) > limit * 2:", "parts may be twice the limit"),
    ("K-e", P, "            info = zipfile.ZipInfo(name, ZIP_TIME)", "            info = zipfile.ZipInfo(name, time.localtime(time.time())[:6])", "the time of packing enters the zip files"),
    ("K-f", P, "    if args.out.exists():", "    if False:", "an existing output folder is used"),

    # OK-  harmless changes: the tests must keep passing
    ("OK-a", C, 'err(f"missing file: {name}")', 'err(f"{name} is not there")', "another wording of a message of the check"),
    ("OK-b", I, 'stop(f"{path.relative_to(tables)}: the run is done but has no branch set")', 'stop(f"{path.relative_to(tables)}: no branch set although the run is done")', "another wording of a message of the import"),
    ("OK-c", P, "MAX_BYTES = 19 * 2**30 // 10      # 1.9 GiB; GitHub accepts attachments below 2 GiB", "MAX_BYTES = 18 * 2**30 // 10      # 1.8 GiB", "a smaller default size of a part"),
    ("OK-d", D, "def fmt(x):\n    return str(int(x)) if float(x).is_integer() else f\"{x:.1f}\"", "def fmt(x):\n    return str(int(x)) if x == int(x) else f\"{x:.1f}\"", "the same number format written another way"),
]
