#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Agent Software Testing authors
"""Add the results of a replayed submission to the data.

    python3 scripts/publish_submission.py candidate   needs PLAN_FILE, DATA_DIR, OUT_FILE
    python3 scripts/publish_submission.py publish     needs GH_TOKEN, REPO, PR_NUMBER, HEAD_SHA, SUBMITTED
    python3 scripts/publish_submission.py withdraw    needs SYSTEM_ID

`candidate` adds the results in this working tree and pushes nothing, for the preview after a replay. `publish` runs
after a maintainer merged a submission: it reads the artifacts of the replay of exactly the merged commit as data, adds
them and pushes. `withdraw` marks a published entry as withdrawn and pushes. None of them runs anything from a
submission. If anything is wrong nothing is pushed and the pull request says why. docs/REPLAY-WORKFLOW.md has the details.
"""
from __future__ import annotations

import csv
import datetime
import gzip
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_job as rj  # noqa: E402
import validate_manifest  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MARKER = "<!-- publish-submission -->"
MAX_ARTIFACT_BYTES = 1 << 30
MAX_MEMBER_BYTES = 1 << 28
SET_SCHEMA = "agent-software-testing/branch-set@0.1"
STANDARD_BUDGET_S = 21600
BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
PUSH_ATTEMPTS = 5
# When to ask a person to look. These never stop a submission: an unusual result can be a good one.
FLAG_ABOVE_BEST = 1.5
FLAG_REPEAT_SPREAD = 0.5
FLAG_TIMEOUT_SHARE = 0.2
FLAG_FLAKE_SHARE = 0.01


class PublishError(Exception):
    """A stop with a sentence for the pull request."""


# ---------------------------------------------------------------------------------------------------------------------
# Finding the passed replay
# ---------------------------------------------------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download_zip(token: str, repo: str, artifact_id: int, base: str = "https://api.github.com") -> bytes:
    """The zip of one artifact. GitHub answers with a redirect to storage that must not receive the token."""
    request = urllib.request.Request(f"{base}/repos/{repo}/actions/artifacts/{artifact_id}/zip")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=120) as response:
            return response.read(MAX_ARTIFACT_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code not in (301, 302, 303, 307, 308) or not exc.headers.get("Location", "").startswith("https://"):
            raise
        location = exc.headers["Location"]
    with urllib.request.urlopen(location, timeout=300) as response:
        data = response.read(MAX_ARTIFACT_BYTES + 1)
    if len(data) > MAX_ARTIFACT_BYTES:
        raise PublishError("An artifact of the replay is larger than this step reads.")
    return data


def read_member(archive: bytes, name: str) -> bytes:
    """One named file of an artifact. Nothing is unpacked to disk, so a name inside the zip cannot reach anywhere."""
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        try:
            info = zf.getinfo(name)
        except KeyError:
            raise PublishError(f"An artifact of the replay does not hold {name}.") from None
        if info.file_size > MAX_MEMBER_BYTES:
            raise PublishError(f"{name} in an artifact of the replay is too large.")
        return zf.read(info)


def find_replay(api: Callable, fetch: Callable[[int], bytes], repo: str, pr: int, head_sha: str) -> tuple[dict, dict]:
    """The plan and the artifacts by name of a passed replay of exactly this commit of this pull request."""
    status, body = rj.api_retry(api, "GET", f"/repos/{repo}/actions/workflows/replay.yml/runs?head_sha={head_sha}"
                                            "&event=pull_request_target&status=success&per_page=100")
    if status != 200:
        raise PublishError("GitHub did not list the replay runs of this pull request.")
    runs = json.loads(body).get("workflow_runs", [])
    runs = [r for r in runs if isinstance(r, dict) and r.get("conclusion") == "success"]
    expired = False
    for run in sorted(runs, key=lambda r: int(r["id"]), reverse=True):
        artifacts: dict[str, dict] = {}
        page = 1
        while page <= 30:
            status, body = rj.api_retry(api, "GET", f"/repos/{repo}/actions/runs/{int(run['id'])}/artifacts?per_page=100&page={page}")
            if status != 200:
                raise PublishError("GitHub did not list the artifacts of the replay.")
            listed = json.loads(body).get("artifacts", [])
            artifacts.update({a["name"]: a for a in listed if isinstance(a, dict)})
            if len(listed) < 100:
                break
            page += 1
        if "prepare" not in artifacts or artifacts["prepare"].get("expired"):
            expired = True
            continue
        plan = json.loads(read_member(fetch(int(artifacts["prepare"]["id"])), "plan.json"))
        if plan.get("pr") == pr and plan.get("head_sha") == head_sha:
            return plan, artifacts
    if expired:
        raise PublishError("The results of the replay have expired (GitHub keeps them for 30 days). Open a new pull request "
                           "that changes the manifest, a note is enough. Its replay and its merge then publish the submission.")
    raise PublishError("There is no passed replay of the merged commit, so nothing was published. "
                       "A submission is published only when the replay of its last commit passed.")


# ---------------------------------------------------------------------------------------------------------------------
# Adding the results
# ---------------------------------------------------------------------------------------------------------------------

def read_table(path: Path) -> tuple[list[str], list[dict]]:
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    return list(reader.fieldnames or []), rows


def append_rows(path: Path, header: list[str], rows: list[dict]) -> None:
    with open(path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, lineterminator="\n")
        writer.writerows(rows)


def text_or(value: Any, fallback: str, limit: int) -> str:
    return rj.clean(value or "", limit) or rj.clean(fallback, limit)


def tool_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug if re.match(r"^[a-z]", slug) else f"tool-{slug}".rstrip("-")


def label_of(document: dict) -> str:
    tool, budget = document["tool"], document["budget"]
    parts = [f"{rj.clean(tool['name'], 80)} {rj.clean(tool['version'], 40)}"]
    if tool.get("model"):
        parts.append(rj.clean(tool["model"], 60))
    if budget["wall_s"] != STANDARD_BUDGET_S:
        cores = budget.get("cores")
        hours = budget["wall_s"] / 3600
        if cores:
            parts.append(f"{cores} core" + ("s" if cores != 1 else ""))
        parts.append(f"{hours:g} h")
    return ", ".join(parts)


def set_bytes(program: str, coverage: dict) -> bytes:
    keys = coverage.get("covered_branch_keys")
    if coverage.get("program") != program or not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
        raise PublishError(f"The branch set of {program} in the replay is not usable.")
    document = {"schema": SET_SCHEMA, "program": program, "branch_key_format": coverage.get("branch_key_format"),
                "branch_total": coverage.get("branch_total"), "branch_covered": coverage.get("branch_covered"),
                "covered_branch_keys": keys}
    return gzip.compress(json.dumps(document, separators=(",", ":")).encode(), 9, mtime=0)


def curve_text(timeline: bytes) -> tuple[str, int]:
    """The curve as the release writes it, and its largest fall between two checkpoints."""
    try:
        rows = list(csv.reader(io.StringIO(timeline.decode("utf-8"))))
        points = [(int(a), int(b)) for a, b in rows[1:]]
    except (UnicodeError, ValueError, csv.Error):
        raise PublishError("A curve of the replay is not usable.") from None
    if not points or rows[0] != ["t_s", "covered"]:
        raise PublishError("A curve of the replay is not usable.")
    dip = max((a[1] - b[1] for a, b in zip(points, points[1:])), default=0)
    return "t_s,covered\n" + "".join(f"{t},{c}\n" for t, c in points), max(dip, 0)


def flags_for(program: str, runs: list[dict], best: int | None) -> list[str]:
    """What a person should look at in the runs of one program. Each run is the replay.json of a repeat."""
    flags = []
    covered = sorted(int(r.get("branch_covered") or 0) for r in runs)
    median = covered[len(covered) // 2]
    if median == 0:
        flags.append(f"{program}: the runs cover no branch.")
    if best and median > FLAG_ABOVE_BEST * best:
        flags.append(f"{program}: the median of {median} branches is far above the best so far ({best}).")
    if median and covered[-1] - covered[0] > FLAG_REPEAT_SPREAD * median:
        flags.append(f"{program}: the repeats differ a lot ({covered[0]} to {covered[-1]} branches).")
    for repeat, run in enumerate(runs, 1):
        statuses = run.get("status_counts") if isinstance(run.get("status_counts"), dict) else {}
        total = sum(v for v in statuses.values() if isinstance(v, int))
        timeouts = statuses.get("timeout") or 0
        if total and timeouts > FLAG_TIMEOUT_SHARE * total:
            flags.append(f"{program} repeat {repeat}: {timeouts} of {total} cases hit their time limit.")
        flake = (run.get("flake") or {}).get("branches_differing") if isinstance(run.get("flake"), dict) else None
        if isinstance(flake, int) and flake > max(10, FLAG_FLAKE_SHARE * int(run.get("branch_covered") or 0)):
            flags.append(f"{program} repeat {repeat}: {flake} branches differ when the cases are replayed again.")
    return flags


def add_results(root: Path, stem: str, document: dict, plan: dict, member: Callable[[str, str], bytes], submitted: str,
                today: datetime.date) -> list[str]:
    """Write the results into the tree at ``root``, regenerate and check. Returns what a person should look at."""
    problems = validate_manifest.validate(document, json.loads((root / "submissions/schema.json").read_text(encoding="utf-8")))
    if problems:
        raise PublishError("The manifest does not follow submissions/schema.json.")
    wanted = {(b["program"], b["repeat"]) for b in document["bundles"]}
    planned = {(r["program"], r["repeat"]) for r in plan["runs"]}
    if wanted != planned or plan.get("system_id") != stem:
        raise PublishError("The manifest lists other runs than the replay did.")

    release = json.loads((root / "RELEASE.json").read_text(encoding="utf-8"))
    tool = document["tool"]
    tools_header, tools = read_table(root / "tools.csv")
    known = {t["tool_id"]: t for t in tools}
    tool_id = tool_slug(tool["name"])
    if tool_id in known and known[tool_id]["name"].casefold() != tool["name"].casefold():
        tool_id = f"{tool_id}-{stem}".replace("_", "-")
    if tool_id not in known:
        append_rows(root / "tools.csv", tools_header, [{
            "tool_id": tool_id, "name": rj.clean(tool["name"], 80), "url": tool["url"], "reference": tool["url"],
            "description": text_or(document.get("notes"), f"{tool['name']} {tool['version']}.", 300)}])
    systems_header, systems = read_table(root / "systems.csv")
    if stem in {s["system_id"] for s in systems}:
        raise PublishError(f"The system {stem} is already published.")
    tokens = document.get("tokens") or {}
    append_rows(root / "systems.csv", systems_header, [{
        "system_id": stem, "label": label_of(document), "family": tool["family"], "backend": "other",
        "model": rj.clean(tool.get("model") or "", 60), "cores": document["budget"].get("cores", ""),
        "budget_s": document["budget"]["wall_s"], "track": document["track"], "measure": "standard", "group": "main",
        "description": text_or(document.get("notes"), f"{tool['name']} {tool['version']}.", 1000),
        "reference": tool["url"], "tool_id": tool_id, "version": rj.clean(tool["version"], 100), "config": "unknown",
        "config_sha256": tool.get("config_sha256") or "", "submitted": submitted, "first_release": release["release"],
        "status": "current", "supersedes": ""}])
    manifest_path = root / "submissions" / f"{stem}.json"
    if not manifest_path.is_file():
        manifest_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    order = [p["program"] for p in read_table(root / "programs.csv")[1]]
    best: dict[str, int] = {}
    for row in read_table(root / "derived/finals.csv")[1]:
        best[row["program"]] = max(best.get(row["program"], 0), int(float(row["median"])))
    repeats_of: dict[str, list[int]] = {}
    for program, repeat in wanted:
        repeats_of.setdefault(program, []).append(repeat)
    runs_header, _rows = read_table(root / "runs.csv")
    new_runs = []
    flags: list[str] = []
    for program in sorted(repeats_of, key=order.index):
        replays = []
        for repeat in sorted(repeats_of[program]):
            run = f"{program}-r{repeat}"
            artifact = f"data-coverage-{run}"
            replay = json.loads(member(artifact, "replay.json"))
            coverage = json.loads(member(artifact, "coverage-branches.json"))
            curve, dip = curve_text(member(artifact, "timeline.csv"))
            set_path = f"sets/{stem}/{program}/r{repeat}.json.gz"
            curve_path = f"curves/{stem}/{program}/r{repeat}.csv"
            for path, data in ((set_path, set_bytes(program, coverage)), (curve_path, curve.encode())):
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_bytes(data)
            notes = []
            if len(repeats_of[program]) < 3:
                notes.append("incomplete")
            if dip:
                notes.append(f"curve dips by up to {dip} branches")
            replays.append(replay)
            new_runs.append({"system_id": stem, "program": program, "repeat": repeat, "covered": replay.get("branch_covered"),
                             "total": replay.get("branch_total"), "measure": "standard", "set_path": set_path,
                             "curve_path": curve_path, "output_tokens": tokens.get("output_tokens", ""), "notes": "; ".join(notes)})
        flags += flags_for(program, replays, best.get(program))
    append_rows(root / "runs.csv", runs_header, new_runs)

    for program in sorted(repeats_of, key=order.index):
        with tempfile.TemporaryDirectory() as folder:
            rows_file = Path(folder) / "signature-rows.csv"
            rows_file.write_bytes(member(f"data-signatures-{program}", "signature-rows.csv"))
            run_script(root, "add_signatures.py", stem, program, "--rows", str(rows_file))
    run_script(root, "derive.py")

    counts = dict(release["counts"])
    counts.update(systems=counts["systems"] + 1, runs=counts["runs"] + len(new_runs),
                  runs_with_branch_sets=counts["runs_with_branch_sets"] + len(new_runs),
                  runs_with_curves=counts["runs_with_curves"] + len(new_runs))
    pins = json.loads((root / ".github/replay-pins.json").read_text(encoding="utf-8"))
    release.update(date=today.isoformat(), image=str(pins["image"]).rpartition("@sha256:")[2], counts=counts)
    (root / "RELEASE.json").write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
    run_script(root, "check.py")
    return flags


def run_script(root: Path, script: str, *args: str) -> None:
    done = subprocess.run([sys.executable, str(root / "scripts" / script), *args], cwd=root, text=True, capture_output=True)
    if done.returncode:
        lines = [line for line in (done.stdout + "\n" + done.stderr).splitlines() if line.strip()]
        raise PublishError(f"{script} refused the new data: " + "; ".join(rj.clean(line, 200) for line in lines[-4:]))


# ---------------------------------------------------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------------------------------------------------

def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, text=True, capture_output=True).stdout.strip()


def commit_and_push(root: Path, message: str, paths: list[str]) -> bool:
    """Commit and push. False when another push came first, so that the caller starts again from the new main branch."""
    git(root, "add", "-A", "--", *paths)
    git(root, "-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}", "commit", "-q", "-m", message)
    try:
        git(root, "push", "origin", "HEAD:main")
        return True
    except subprocess.CalledProcessError:
        git(root, "fetch", "-q", "origin", "main")
        git(root, "reset", "-q", "--hard", "origin/main")
        return False


def say(api: Callable, repo: str, pr: int, text: str) -> None:
    status, _body = rj.api_retry(api, "POST", f"/repos/{repo}/issues/{pr}/comments", body={"body": f"{MARKER}\n{text}"})
    if status >= 300:
        print(f"GitHub refused the comment (status {status})", file=sys.stderr)


def manifest_of_pull_request(api: Callable, repo: str, pr: int) -> str | None:
    """The name of the one manifest that the pull request changed, if it changed one."""
    status, body = rj.api_retry(api, "GET", f"/repos/{repo}/pulls/{pr}/files?per_page=100")
    if status != 200:
        raise PublishError("GitHub did not list the files of this pull request.")
    names = [f["filename"] for f in json.loads(body) if isinstance(f, dict) and isinstance(f.get("filename"), str)]
    found = [n[len("submissions/"):-len(".json")] for n in names if re.fullmatch(r"submissions/[^/]+\.json", n)
             and n not in ("submissions/schema.json", "submissions/example.json")]
    return found[0] if len(found) == 1 else None


def publish(api: Callable, fetch: Callable[[int], bytes], repo: str, pr: int, head_sha: str, submitted: str,
            root: Path = ROOT, today: datetime.date | None = None) -> str | None:
    """Add the results to the tree at ``root``. Returns the system, or None when there is nothing to do: the pull request
    changed a manifest that is already published, or changed no manifest."""
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    named = manifest_of_pull_request(api, repo, pr)
    if named is None or named in {s["system_id"] for s in read_table(root / "systems.csv")[1]}:
        return None
    plan, artifacts = find_replay(api, fetch, repo, pr, head_sha)
    stem = plan.get("system_id")
    if stem != named or not rj.NAME_RE.match(stem):
        raise PublishError("The replay ran another manifest than the one this pull request changed.")
    path = root / "submissions" / f"{stem}.json"
    if not path.is_file():
        raise PublishError(f"submissions/{stem}.json is not on the main branch.")
    document = json.loads(path.read_text(encoding="utf-8"))
    cache: dict[str, bytes] = {}

    def member(artifact: str, name: str) -> bytes:
        entry = artifacts.get(artifact)
        if entry is None or entry.get("expired"):
            raise PublishError(f"The artifact {artifact} of the replay is missing or has expired.")
        if artifact not in cache:
            cache[artifact] = fetch(int(entry["id"]))
        return read_member(cache[artifact], name)

    add_results(root, stem, document, plan, member, submitted, today)
    return stem


def candidate(plan_file: Path, data_dir: Path, today: datetime.date, root: Path = ROOT) -> dict:
    """Add the results of a finished replay to this working tree, for the preview. Pushes nothing. Returns the record for
    the comment: {"ok", "flags", "problem"}."""
    try:
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
        document, stem = plan["manifest"], plan["system_id"]
        if not rj.NAME_RE.match(stem):
            raise PublishError("The plan of the replay does not name a system.")

        def member(artifact: str, name: str) -> bytes:
            path = data_dir / artifact / name
            if not re.fullmatch(r"data-[a-z]+-[a-z0-9_-]+", artifact) or path.is_symlink() or not path.is_file():
                raise PublishError(f"The results of the replay do not hold {name} of {artifact}.")
            if path.stat().st_size > MAX_MEMBER_BYTES:
                raise PublishError(f"{name} in the results of the replay is too large.")
            return path.read_bytes()

        flags = add_results(root, stem, document, plan, member, today.isoformat(), today)
        return {"ok": True, "flags": flags, "problem": ""}
    except PublishError as exc:
        return {"ok": False, "flags": [], "problem": str(exc)}
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        return {"ok": False, "flags": [], "problem": f"The step stopped with an error ({type(exc).__name__})."}


def withdraw(root: Path, system: str) -> None:
    """Mark a published entry as withdrawn. It stays in the data and leaves the comparison."""
    header, rows = read_table(root / "systems.csv")
    match = [r for r in rows if r["system_id"] == system]
    if not match:
        raise PublishError(f"There is no system called {system}.")
    match[0]["status"] = "withdrawn"
    with open(root / "systems.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    run_script(root, "derive.py")
    run_script(root, "check.py")


def main(argv: list[str]) -> int:
    command = argv[0] if argv else ""
    today = datetime.datetime.now(datetime.timezone.utc).date()
    if command == "candidate":
        record = candidate(Path(os.environ["PLAN_FILE"]), Path(os.environ["DATA_DIR"]), today)
        Path(os.environ["OUT_FILE"]).write_text(json.dumps(record), encoding="utf-8")
        print("candidate:", "ok" if record["ok"] else record["problem"])
        return 0 if record["ok"] else 1
    if command == "publish":
        repo, pr, head_sha = os.environ["REPO"], int(os.environ["PR_NUMBER"]), os.environ["HEAD_SHA"]
        token = os.environ["GH_TOKEN"]
        api = rj.github_api(token, os.environ.get("GITHUB_API_URL", "https://api.github.com"))
        try:
            for _attempt in range(PUSH_ATTEMPTS):
                stem = publish(api, lambda artifact: download_zip(token, repo, artifact), repo, pr, head_sha, os.environ["SUBMITTED"][:10])
                if stem is None:
                    print("nothing to publish")
                    return 0
                if commit_and_push(ROOT, f"Add {stem}", ["runs.csv", "systems.csv", "tools.csv", "derived", "README.md", "RELEASE.json", f"sets/{stem}", f"curves/{stem}"]):
                    say(api, repo, pr, f"**Published.** `{stem}` is in the data. The website follows within a few minutes.")
                    return 0
            raise PublishError("The main branch kept moving, so the results could not be pushed.")
        except PublishError as exc:
            say(api, repo, pr, f"**Not published.** {exc} Nothing was changed. A maintainer can re-run this job once it is fixed.")
            print(exc, file=sys.stderr)
            return 1
        except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError, zipfile.BadZipFile) as exc:
            say(api, repo, pr, f"**Not published.** The step stopped with an error ({type(exc).__name__}). Nothing was changed. "
                               "A maintainer can look at the run and re-run this job.")
            raise
    if command == "withdraw":
        system = os.environ["SYSTEM_ID"]
        try:
            for _attempt in range(PUSH_ATTEMPTS):
                withdraw(ROOT, system)
                if commit_and_push(ROOT, f"Withdraw {system}", ["systems.csv", "derived", "README.md"]):
                    print(f"withdrew {system}")
                    return 0
        except PublishError as exc:
            print(exc, file=sys.stderr)
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
