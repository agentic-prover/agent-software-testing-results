#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Agent Software Testing authors
"""Pack the test inputs of runs into zip parts that fit a GitHub release attachment.

The input is a folder that holds bundles (tool.json, cases.jsonl and blobs/; SPEC section 5 of the `agent-software-testing`
repository), one per run, anywhere below it; they should have passed `python -m agent_software_testing validate`, which this
script does not repeat. The output is a new folder with

    <entry>_<program>_r<run>_part<k>of<n>.zip   one standalone part of a run
    manifest.json     a list with one object per part: program, entry, run, part, parts, url, sha256, bytes
    SHA256SUMS        sha256 and name of every part, in the format of `sha256sum -c`

A part holds one folder named like the zip file without `.zip`. The folder is a valid bundle of consecutive cases of
the run: the run's tool.json unchanged, the lines of cases.jsonl unchanged and in order, and the blobs those cases
refer to (a blob that several parts need is stored in each). A case is never split; a run that does not fit one part
becomes several, numbered from 1. Unzip each part into a folder of its own. `url` stays empty until the part is
uploaded.

Program and run are those of tool.json (`program.id`, `repeat`). The entry is the tool name, version and model of
tool.json, joined and lower-cased, for example `agolic-1.0.1-gpt-5.5`. The configuration hash is not part of it
because one system can have a different one for each program. Two bundles with the same program, entry and run are
refused: give the systems different names.

RESTRICTED lists the programs whose inputs are held back from a release (DISCLOSURE.md). Their runs are skipped
(only tool.json is read) and listed. --restricted replaces the list, or empties it when given no names.

The size limit is on the zip file. The split works with an upper bound of the size of a part, so parts are usually
smaller. A case that alone exceeds the limit still gets a part of its own. Parts are deterministic: sorted names, fixed
times and attributes, one compression level; the same bundles give the same bytes with one version of zlib.

Usage:  python scripts/package_inputs.py BUNDLES OUT [--max-bytes N] [--restricted [PROGRAM ...]]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import zipfile
from pathlib import Path

RESTRICTED = ["bc", "diff", "gawk", "gcal", "jq", "oggenc", "xorriso"]
MAX_BYTES = 19 * 2**30 // 10      # 1.9 GiB; GitHub accepts attachments below 2 GiB
ZIP_TIME = (1980, 1, 1, 0, 0, 0)  # the earliest time a zip file can hold


def slug(text):
    """Lower case, with every run of characters outside a-z 0-9 . _ - replaced by one hyphen."""
    return re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-._")


def find_bundles(root):
    """Every folder at or below root that holds a tool.json, in sorted order; a bundle is not entered."""
    for here, dirs, files in os.walk(root):
        dirs.sort()
        if "tool.json" in files:
            dirs.clear()
            yield Path(here)


def blob_names(line):
    """The blobs that the case in a line of cases.jsonl refers to: standard input and the file entries."""
    case = json.loads(line)
    refs = [case.get("stdin")] + [f.get("blob") for f in case.get("files", [])]
    return {ref.removeprefix("sha256:") for ref in refs if ref}


def grown(n):
    """An upper bound of the deflate output for n bytes of any content (incompressible data grows by 0.03 percent)."""
    return n + n // 1000 + 1


def zip_bound(name, n):
    """An upper bound of the bytes that a file of n bytes adds to a zip: its data, the local and the central header
    (76 bytes and the name twice), the zip64 extensions (48 bytes) and some slack."""
    return grown(n) + 2 * len(name) + 160


def split(lines, blobs, stem, tool_size, limit):
    """The lines of cases.jsonl in consecutive parts (lists of lines) whose zip files stay below limit. `blobs` is the
    folder of the blobs and `stem` the longest folder name a part can have."""
    def cost(line, names):
        return grown(len(line)) + sum(zip_bound(f"{stem}/blobs/{b}", (blobs / b).stat().st_size) for b in names)

    base = 100 + zip_bound(f"{stem}/tool.json", tool_size) + zip_bound(f"{stem}/cases.jsonl", 0)  # 100: end of zip
    parts, used, held = [[]], base, set()
    for line in lines:
        names = blob_names(line)
        if parts[-1] and used + cost(line, names - held) > limit:
            parts.append([])
            used, held = base, set()
        used += cost(line, names - held)
        held |= names
        parts[-1].append(line)
    return parts


def write_part(path, stem, tool, lines, blobs):
    """Write one part: the folder `stem` with tool.json, cases.jsonl and the blobs of `lines`."""
    files = {f"{stem}/tool.json": tool, f"{stem}/cases.jsonl": b"".join(lines)}
    for name in set().union(*map(blob_names, lines)):
        files[f"{stem}/blobs/{name}"] = blobs / name
    with zipfile.ZipFile(path, "x") as z:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            data = files[name]
            z.writestr(info, data if isinstance(data, bytes) else data.read_bytes())


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Pack the test inputs of runs into zip parts for a release.")
    ap.add_argument("bundles", type=Path, help="folder that holds the bundles")
    ap.add_argument("out", type=Path, help="new folder for the parts, manifest.json and SHA256SUMS")
    ap.add_argument("--max-bytes", type=int, default=MAX_BYTES, help="size limit of a part (default: 1.9 GiB)")
    ap.add_argument("--restricted", nargs="*", default=RESTRICTED, metavar="PROGRAM",
                    help="programs whose runs are skipped, any case (default: %(default)s; none: --restricted)")
    args = ap.parse_args(argv)
    if args.out.exists():
        raise SystemExit(f"{args.out} exists; name a new folder")

    restricted = {p.lower() for p in args.restricted}
    runs, skipped = {}, []
    for bundle in find_bundles(args.bundles):
        tool = (bundle / "tool.json").read_bytes()
        meta = json.loads(tool)
        program, run = meta["program"]["id"], int(meta["repeat"])
        entry = slug(" ".join(filter(None, [meta["tool"]["name"], meta["tool"].get("version"),
                                            meta["tool"].get("model")])))
        if program.lower() in restricted:
            skipped.append(f"{program} {entry} r{run} ({bundle}): restricted program")
            continue
        base = f"{entry}_{slug(program)}_r{run}"
        if base in runs:
            raise SystemExit(f"{bundle} and {runs[base][3]} are both {program} {entry} r{run}")
        runs[base] = (program, entry, run, bundle, tool)

    args.out.mkdir()
    manifest, sums = [], []
    for base, (program, entry, run, bundle, tool) in sorted(runs.items(), key=lambda kv: kv[1][:3]):
        with open(bundle / "cases.jsonl", "rb") as f:
            lines = f.readlines()
        n = max(len(lines), 1)
        parts = split(lines, bundle / "blobs", f"{base}_part{n}of{n}", len(tool), args.max_bytes)
        for k, part in enumerate(parts, 1):
            name = f"{base}_part{k}of{len(parts)}.zip"
            write_part(args.out / name, name[:-4], tool, part, bundle / "blobs")
            digest, size = sha256_file(args.out / name), (args.out / name).stat().st_size
            manifest.append({"program": program, "entry": entry, "run": run, "part": k, "parts": len(parts),
                             "url": "", "sha256": digest, "bytes": size})
            sums.append((name, digest))
    (args.out / "manifest.json").write_text("[\n" + ",\n".join(json.dumps(m) for m in manifest) + "\n]\n")
    (args.out / "SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in sorted(sums)))

    print(f"runs {len(runs)}, parts {len(manifest)}, bytes {sum(m['bytes'] for m in manifest):,}, "
          f"skipped {len(skipped)}")
    for s in skipped:
        print(f"  skipped {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
