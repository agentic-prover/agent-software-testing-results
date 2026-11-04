#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The Agent Software Testing authors
"""The logic behind .github/workflows/replay.yml, so that it can be tested without GitHub (standard library only).

    replay_job.py pins        read .github/replay-pins.json and write the image and runner pin to $GITHUB_OUTPUT
    replay_job.py prepare     read the manifest of a pull request through the API, check it and write the plan
    replay_job.py run         replay one bundle (STAGE=coverage) or count its sanitiser signatures (STAGE=signatures)
    replay_job.py finish      make sure a run has a result file, whatever happened before
    replay_job.py comment     post or update the one summary comment of the pull request

Everything that comes from a pull request is data. It reaches this script only through environment variables or files
and is checked before it is used. The script never runs code from a pull request and never starts a shell. Output that
reaches the public log or the public artifacts holds counts and kinds only; the output of the program under test is
written to files that are not uploaded.
"""
from __future__ import annotations

import csv
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import validate_manifest  # noqa: E402

MARKER = "<!-- agent-software-testing-replay -->"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*(?::[A-Za-z0-9._-]+)?(?:@sha256:[0-9a-f]{64})?$")
PINNED_IMAGE_RE = re.compile(r"^ghcr\.io/[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}$")
# Characters allowed in a link written in a manifest: no quotes, spaces, brackets, ampersands, semicolons, dollars,
# backticks, backslashes or other characters that mean something to a shell or to Markdown.
LINK_RE = re.compile(r"^https://[A-Za-z0-9][A-Za-z0-9.-]*(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~%+@,=:-]*)*(?:\?[A-Za-z0-9._~%+@,=:/-]*)?$")
DOI_RE = re.compile(r"^10\.[0-9]{4,9}/[A-Za-z0-9._-]+$")
ZENODO_DOI_RE = re.compile(r"^10\.5281/zenodo\.([0-9]{1,12})$")
ZENODO_PAGE_RE = re.compile(r"^https://zenodo\.org/records?/([0-9]{1,12})/?$")
MAX_LINK = 300
MAX_RUNS = 256            # the most jobs one matrix may hold
MAX_REPEAT = 99
MAX_MANIFEST_BYTES = 1 << 20
RESERVE_BYTES = 2 << 30   # disk kept free for the output and the scratch space of a replay
DOWNLOAD_ATTEMPTS = 3
REPLAY_ATTEMPTS = 2
DEFAULT_BUDGET_S = 340 * 60   # the workflow's job limit is 350 minutes
CHUNK = 1 << 20
STAGES = ("coverage", "signatures")
STATE_PASSED, STATE_FAILED = "passed", "failed"


class Failure(Exception):
    """A stop that has a public message. ``code`` names the message in MESSAGES; ``params`` fill it in."""

    def __init__(self, code: str, **params: Any) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


# ---------------------------------------------------------------------------------------------------------------------
# Text that reaches the public. Everything the submitter wrote is cleaned before it is shown.
# ---------------------------------------------------------------------------------------------------------------------

def clean(value: Any, limit: int = 160) -> str:
    """Printable text without anything that Markdown or GitHub would act on (links, mentions, code, tables, HTML)."""
    text = str(value).replace("[", "(").replace("]", ")")
    text = "".join(ch if ch.isprintable() and ch not in "`|<>@\\#*_~$&" else " " for ch in text)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def code_span(link: str) -> str:
    return "`" + re.sub(r"[^A-Za-z0-9._~:/?=%+@,-]", "", str(link))[:MAX_LINK] + "`"


MESSAGES: dict[str, Callable[[dict], str]] = {
    "pins_missing": lambda p: "The replay setup is not finished. The pinned image or runner version is missing in "
        ".github/replay-pins.json. This is not caused by your submission. A maintainer needs to fix it, then close and reopen this pull request.",
    "no_manifest": lambda p: "The pull request does not add a manifest. Add one file named "
        "submissions/<system_id>.json and nothing else in that folder. See docs/SUBMITTING.md.",
    "several_manifests": lambda p: "The pull request changes several manifests. Send one system per pull request.",
    "manifest_name": lambda p: "The file name of the manifest does not fit. It must be submissions/<system_id>.json with "
        "a system_id of lower-case letters, digits, hyphens and underscores, and system_id inside the file (if present) "
        "must be the same.",
    "github_unavailable": lambda p: "The manifest could not be fetched from GitHub. This looks like a temporary problem "
        "on GitHub's side and is not your fault. A maintainer can re-run the check.",
    "manifest_unreadable": lambda p: "The manifest is not valid JSON or is too large (limit 1 MiB). Detail: "
        + clean(p.get("detail", "")) + ". Fix the file and push again.",
    "manifest_invalid": lambda p: "The manifest does not follow submissions/schema.json. Problems found: "
        + "; ".join(clean(e) for e in p.get("errors", [])) + ". Fix them and push again.",
    "program_unknown": lambda p: f"Program {clean(p.get('program'), 60)} is not one of the programs in programs.csv. "
        "Use the program ids of that file.",
    "duplicate_run": lambda p: f"Program {clean(p.get('program'), 60)}, repeat {clean(p.get('repeat'), 10)} is listed more "
        "than once. Each program and repeat may occur once.",
    "repeat_out_of_range": lambda p: f"Repeat {clean(p.get('repeat'), 10)} of program {clean(p.get('program'), 60)} is "
        f"outside 1 to {MAX_REPEAT}.",
    "too_many_runs": lambda p: f"The manifest lists more than {MAX_RUNS} bundles. GitHub runs at most {MAX_RUNS} jobs from "
        "one matrix. Split the submission into two systems or two pull requests.",
    "link_invalid": lambda p: f"The link of {clean(p.get('program'), 60)} repeat {clean(p.get('repeat'), 10)} is not "
        f"accepted ({clean(p.get('reason'), 80)}). Use an https link or a Zenodo DOI without spaces, quotes, ampersands, "
        "semicolons or other special characters.",
    "link_unsupported": lambda p: f"The link {code_span(p.get('link', ''))} cannot be turned into a download. For a DOI, "
        "only Zenodo records with exactly one file work. Give the direct https link of the archive instead.",
    "download_failed": lambda p: f"The archive could not be downloaded from {code_span(p.get('link', ''))} after "
        f"{int(p.get('attempts', DOWNLOAD_ATTEMPTS))} tries. Check that the link works without logging in. If the host "
        "was down, a maintainer can re-run the failed jobs later.",
    "checksum_mismatch": lambda p: f"The archive at {code_span(p.get('link', ''))} has a SHA-256 "
        f"that starts with {clean(str(p.get('got', ''))[:12], 12)} while the manifest gives one that starts with "
        f"{clean(str(p.get('expected', ''))[:12], 12)}. Check that the link "
        "points to the right file and that the hash in the manifest is of the archive (the .tar.gz), then push again.",
    "too_big_download": lambda p: f"The archive at {code_span(p.get('link', ''))} is too big for the free runner "
        f"({clean(p.get('size_gb'), 10)} GB; at most {clean(p.get('limit_gb'), 10)} GB fits on its disk next to the "
        "unpacked bundle). Make the bundle smaller, for example with fewer or smaller blobs.",
    "too_big_unpacked": lambda p: f"The archive at {code_span(p.get('link', ''))} unpacks to more than "
        f"{clean(p.get('limit_gb'), 10)} GB, which does not fit on the disk of the free runner. Make the bundle smaller.",
    "archive_unreadable": lambda p: f"The file at {code_span(p.get('link', ''))} is not a readable tar archive "
        "(.tar.gz expected). Create it again with tar -czf and check it with tar -tzf.",
    "archive_unsafe": lambda p: "The archive holds an entry that is not allowed (an absolute path, a path with .., a link or "
        "a device file). Create the archive from the bundle folder with only regular files and folders.",
    "bundle_layout": lambda p: "The archive must hold tool.json, cases.jsonl and blobs/ at its top level (not inside "
        "another folder). Create it with tar -czf archive.tar.gz -C path/to/bundle .",
    "bundle_mismatch": lambda p: "tool.json of the bundle disagrees with the manifest in: "
        + ", ".join(clean(f, 30) for f in p.get("fields", [])) + ". Make the two agree and push again.",
    "bundle_invalid": lambda p: "The bundle does not pass the validator (python3 -m agent_software_testing validate). First problems: "
        + "; ".join(clean(e, 200) for e in p.get("errors", [])) + ". Fix the bundle, upload it again and update the hash.",
    "image_unavailable": lambda p: "The replay image could not be pulled. This is not caused by your submission. A "
        "maintainer needs to check the image setting or GitHub Container Registry, then re-run or, after a change of the setting, close and reopen this pull request.",
    "job_limit": lambda p: "The replay of this run reached the time limit of a GitHub job (6 hours) and was stopped. "
        "Make the bundle smaller (fewer cases) or send the run in a smaller form. Nothing was published.",
    "replay_failed": lambda p: "The runner stopped with an error although the bundle is valid, and it did so again on a "
        "second try. This is probably not caused by your bundle. A maintainer will look at it and can re-run the job.",
    "signatures_failed": lambda p: "The sanitiser pass stopped with an error, and again on a second try. This is probably "
        "not caused by your bundle. A maintainer will look at it and can re-run the job.",
    "signature_kind_unknown": lambda p: "The sanitiser pass found an error kind that the project does not list yet. A "
        "maintainer must add the kind to the scripts, then close and reopen this pull request. This is not caused by your submission.",
    "cancelled": lambda p: "This run was cancelled before it finished. A newer commit replaced it, someone stopped it or it reached the time limit of the job. "
        "Push again or ask a maintainer to re-run.",
    "lost": lambda p: "The machine that ran this job stopped or GitHub had a problem, and no result came back. This is not "
        "your fault. A maintainer can re-run the failed jobs.",
    "skipped_stage": lambda p: "Not started. The sanitiser pass starts only after every coverage run has passed.",
    "not_approved": lambda p: "Not started. A maintainer has not approved the replay, or rejected it.",
}


def message(code: str, params: dict | None = None) -> str:
    fn = MESSAGES.get(code)
    if fn is None:
        return "The run stopped for a reason this check does not know."
    try:
        return fn(params or {})
    except (TypeError, ValueError):
        return fn({})


# ---------------------------------------------------------------------------------------------------------------------
# Checks on values taken from a pull request
# ---------------------------------------------------------------------------------------------------------------------

def check_link(value: Any) -> str | None:
    """None when the link or DOI is acceptable; otherwise a short reason."""
    if not isinstance(value, str):
        return "not text"
    if len(value) > MAX_LINK:
        return "too long"
    if DOI_RE.match(value):
        return None
    if not value.startswith("https://"):
        return "not https and not a DOI"
    if not LINK_RE.match(value):
        return "special characters"
    host = urllib.parse.urlsplit(value).hostname or ""
    try:
        ipaddress.ip_address(host)
        return "an address instead of a host name"
    except ValueError:
        pass
    host = host.rstrip(".")
    last = host.rsplit(".", 1)[-1]
    if last.isdigit() or last.startswith("0x"):
        return "an address instead of a host name"
    if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        return "a local host name"
    return None


def read_pins(root: Path = ROOT) -> dict:
    try:
        pins = json.loads((root / ".github" / "replay-pins.json").read_text(encoding="utf-8"))
        image, ref, repo = pins["image"], pins["runner_ref"], pins["runner_repository"]
    except (OSError, ValueError, KeyError, TypeError):
        raise Failure("pins_missing")
    if not (isinstance(image, str) and PINNED_IMAGE_RE.match(image) and isinstance(ref, str) and GIT_SHA_RE.match(ref)
            and isinstance(repo, str) and REPO_RE.match(repo)):
        raise Failure("pins_missing")
    return {"image": image, "runner_ref": ref, "runner_repository": repo}


def known_programs(root: Path = ROOT) -> set[str]:
    try:
        with open(root / "programs.csv", newline="", encoding="utf-8") as handle:
            return {row["program"] for row in csv.DictReader(handle)}
    except (OSError, KeyError):
        return set()


def check_manifest(document: Any, stem: str, programs: set[str], schema: dict | None = None) -> list[dict]:
    """The problems of a manifest, each as {"code", ...params}. Empty when it can be replayed."""
    schema = schema or json.loads((ROOT / "submissions" / "schema.json").read_text(encoding="utf-8"))
    problems: list[dict] = []
    errors = validate_manifest.validate(document, schema)
    if errors:
        return [{"code": "manifest_invalid", "errors": [clean(e, 300) for e in errors[:8]]}]
    if document.get("system_id", stem) != stem:
        problems.append({"code": "manifest_name"})
    bundles = document["bundles"]
    if len(bundles) > MAX_RUNS:
        return problems + [{"code": "too_many_runs"}]
    seen: set[tuple] = set()
    for entry in bundles:
        program, repeat = entry["program"], entry["repeat"]
        if programs and program not in programs:
            problems.append({"code": "program_unknown", "program": program})
        if not 1 <= repeat <= MAX_REPEAT:
            problems.append({"code": "repeat_out_of_range", "program": program, "repeat": repeat})
        if (program, repeat) in seen:
            problems.append({"code": "duplicate_run", "program": program, "repeat": repeat})
        seen.add((program, repeat))
        reason = check_link(entry["doi_or_url"])
        if reason:
            problems.append({"code": "link_invalid", "program": program, "repeat": repeat, "reason": reason})
    return problems


def make_plan(document: dict, stem: str, pr: int, head_sha: str) -> dict:
    runs = []
    for index, entry in enumerate(document["bundles"]):
        runs.append({
            "index": index, "name": f"{entry['program']}-r{entry['repeat']}", "program": entry["program"],
            "repeat": entry["repeat"], "source": entry["doi_or_url"], "sha256": entry["sha256"],
        })
    return {"schema": "replay-plan@1", "pr": pr, "head_sha": head_sha, "system_id": stem, "track": document["track"],
            "tool": {"name": document["tool"]["name"], "version": document["tool"]["version"]}, "runs": runs}


def check_run_entry(run: Any) -> dict:
    """Check one run of a plan again before use (a plan is a file that came through an artifact)."""
    try:
        ok = (isinstance(run["program"], str) and NAME_RE.match(run["program"]) and isinstance(run["repeat"], int)
              and not isinstance(run["repeat"], bool) and 1 <= run["repeat"] <= MAX_REPEAT
              and isinstance(run["sha256"], str) and SHA_RE.match(run["sha256"]) and check_link(run["source"]) is None
              and run["name"] == f"{run['program']}-r{run['repeat']}")
    except (KeyError, TypeError):
        ok = False
    if not ok:
        raise Failure("link_invalid", program="?", repeat="?", reason="the plan entry is not valid")
    return run


# ---------------------------------------------------------------------------------------------------------------------
# GitHub API
# ---------------------------------------------------------------------------------------------------------------------

def github_api(token: str, base: str = "https://api.github.com") -> Callable:
    def call(method: str, path: str, body: Any = None, raw: bool = False, limit: int | None = None) -> tuple[int, bytes]:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(base + path, data=data, method=method)
        request.add_header("Authorization", f"Bearer {token}")
        request.add_header("Accept", "application/vnd.github.raw+json" if raw else "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, response.read(limit + 1 if limit else None)
        except urllib.error.HTTPError as exc:
            return exc.code, b""
    return call


def api_retry(api: Callable, *args: Any, attempts: int = 3, sleep: Callable = time.sleep, **kwargs: Any) -> tuple[int, bytes]:
    last: tuple[int, bytes] = (0, b"")
    for attempt in range(attempts):
        try:
            last = api(*args, **kwargs)
        except (urllib.error.URLError, OSError):
            last = (0, b"")
        if last[0] and last[0] < 500 and last[0] != 429:
            return last
        if attempt + 1 < attempts:
            sleep(5 * (attempt + 1))
    return last


def prepare(api: Callable, repo: str, pr: int, head_sha: str, head_repo: str, root: Path = ROOT,
            sleep: Callable = time.sleep) -> dict:
    """Read the changed files of the pull request and the manifest at its head commit. Returns the prepare record."""
    record: dict = {"ok": False, "problems": [], "system_id": None, "runs": [], "programs": [], "incomplete": [],
                    "plan": None}
    try:
        read_pins(root)
    except Failure as exc:
        record["problems"].append({"code": exc.code})
        return record
    if not (REPO_RE.match(repo) and GIT_SHA_RE.match(head_sha) and REPO_RE.match(head_repo) and pr > 0):
        record["problems"].append({"code": "github_unavailable"})
        return record
    names: list[str] = []
    page = 1
    while page <= 30:
        status, body = api_retry(api, "GET", f"/repos/{repo}/pulls/{pr}/files?per_page=100&page={page}", sleep=sleep)
        if status != 200:
            record["problems"].append({"code": "github_unavailable"})
            return record
        try:
            files = json.loads(body)
        except ValueError:
            files = []
        names += [f["filename"] for f in files if isinstance(f, dict) and isinstance(f.get("filename"), str)
                  and f.get("status") in ("added", "modified", "renamed", "changed", "copied")]
        if len(files) < 100:
            break
        page += 1
    candidates = [n for n in names if re.fullmatch(r"submissions/[^/]+\.json", n)
                  and n not in ("submissions/schema.json", "submissions/example.json")]
    if not candidates:
        record["problems"].append({"code": "no_manifest"})
        return record
    if len(candidates) > 1:
        record["problems"].append({"code": "several_manifests"})
        return record
    stem = candidates[0][len("submissions/"):-len(".json")]
    if not NAME_RE.match(stem) or len(stem) > 100:
        record["problems"].append({"code": "manifest_name"})
        return record
    quoted = urllib.parse.quote(candidates[0])
    body = b""
    for source in dict.fromkeys([head_repo, repo]):
        status, body = api_retry(api, "GET", f"/repos/{source}/contents/{quoted}?ref={head_sha}", raw=True,
                                 limit=MAX_MANIFEST_BYTES, sleep=sleep)
        if status == 200:
            break
    else:
        record["problems"].append({"code": "github_unavailable"})
        return record
    if len(body) > MAX_MANIFEST_BYTES:
        record["problems"].append({"code": "manifest_unreadable", "detail": "larger than 1 MiB"})
        return record
    try:
        document = json.loads(body.decode("utf-8"))
    except (ValueError, RecursionError) as exc:
        record["problems"].append({"code": "manifest_unreadable", "detail": type(exc).__name__ + ": " + str(exc)[:100]})
        return record
    problems = check_manifest(document, stem, known_programs(root))
    record["system_id"] = stem
    if problems:
        record["problems"] = problems
        return record
    plan = make_plan(document, stem, pr, head_sha)
    counts: dict[str, int] = {}
    for run in plan["runs"]:
        counts[run["program"]] = counts.get(run["program"], 0) + 1
    record.update(ok=True, plan=plan, programs=sorted(counts), incomplete=sorted(p for p, n in counts.items() if n < 3),
                  runs=[{k: r[k] for k in ("index", "name", "program", "repeat")} for r in plan["runs"]])
    return record


# ---------------------------------------------------------------------------------------------------------------------
# Download and unpack
# ---------------------------------------------------------------------------------------------------------------------

class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urllib.parse.urlsplit(newurl)
        host = parts.hostname or ""
        try:
            ipaddress.ip_address(host)
            literal = True
        except ValueError:
            literal = False
        if parts.scheme != "https" or literal or not reaches_only_public_addresses(host):
            raise urllib.error.URLError("redirect to a link that is not allowed")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def reaches_only_public_addresses(host: str) -> bool:
    """The addresses a host name stands for are all on the public internet, not on the runner's own network."""
    try:
        found = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return False
    return bool(found) and all(ipaddress.ip_address(item[4][0]).is_global for item in found)


class Net:
    """Opens https links only. Tests replace it."""

    def open(self, url: str, timeout: float = 60):
        if not reaches_only_public_addresses(urllib.parse.urlsplit(url).hostname or ""):
            raise urllib.error.URLError("the link does not lead to the public internet")
        opener = urllib.request.build_opener(_HttpsOnly)
        request = urllib.request.Request(url, headers={"User-Agent": "agent-software-testing-replay-workflow"})
        return opener.open(request, timeout=timeout)


def resolve_link(source: str, net: Any, sleep: Callable = time.sleep) -> str:
    """The address to download. A Zenodo DOI or record page is looked up in the Zenodo API."""
    match = ZENODO_DOI_RE.match(source) or ZENODO_PAGE_RE.match(source)
    if match is None:
        if DOI_RE.match(source):
            raise Failure("link_unsupported", link=source)
        return source
    api = f"https://zenodo.org/api/records/{match.group(1)}"
    for attempt in range(DOWNLOAD_ATTEMPTS):
        try:
            with net.open(api) as response:
                record = json.loads(response.read(MAX_MANIFEST_BYTES))
            files = record["files"]
            if not isinstance(files, list) or len(files) != 1:
                raise Failure("link_unsupported", link=source)
            target = files[0]["links"]["self"]
            if check_link(target) is not None or urllib.parse.urlsplit(target).hostname != "zenodo.org":
                raise Failure("link_unsupported", link=source)
            return target
        except Failure:
            raise
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
            if attempt + 1 < DOWNLOAD_ATTEMPTS:
                sleep(5 * (attempt + 1))
    raise Failure("download_failed", link=source, attempts=DOWNLOAD_ATTEMPTS)


def gb(n: float) -> str:
    return f"{n / (1 << 30):.1f}"


def download(url: str, shown: str, dest: Path, expected: str, max_bytes: int, net: Any, sleep: Callable = time.sleep) -> int:
    """Download to ``dest`` and check the SHA-256. Retries a few times; the failure names the link in the manifest."""
    for attempt in range(DOWNLOAD_ATTEMPTS):
        digest = hashlib.sha256()
        size = 0
        try:
            with net.open(url) as response:
                declared = response.headers.get("Content-Length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise Failure("too_big_download", link=shown, size_gb=gb(int(declared)), limit_gb=gb(max_bytes))
                with open(dest, "wb") as out:
                    while True:
                        chunk = response.read(CHUNK)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > max_bytes:
                            raise Failure("too_big_download", link=shown, size_gb=gb(size), limit_gb=gb(max_bytes))
                        digest.update(chunk)
                        out.write(chunk)
                if declared and declared.isdigit() and int(declared) != size:
                    raise OSError("short download")
        except Failure:
            raise
        except (urllib.error.URLError, OSError, ValueError):
            if attempt + 1 < DOWNLOAD_ATTEMPTS:
                sleep(5 * (attempt + 1))
            continue
        if digest.hexdigest() != expected:
            raise Failure("checksum_mismatch", link=shown, got=digest.hexdigest(), expected=expected)
        return size
    raise Failure("download_failed", link=shown, attempts=DOWNLOAD_ATTEMPTS)


def extract(archive: Path, dest: Path, max_total: int, shown: str) -> int:
    """Unpack regular files and folders only. Nothing outside ``dest``, no links, no devices, a size limit."""
    total = 0
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive, "r:*") as tar:
            for member in tar:
                name = member.name
                parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
                if name.startswith("/") or ".." in parts or "\x00" in name or "\\" in name:
                    raise Failure("archive_unsafe")
                if not parts:
                    continue
                target = dest.joinpath(*parts)
                if not str(target.resolve()).startswith(str(dest) + os.sep):
                    raise Failure("archive_unsafe")
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    os.chmod(target, 0o755)
                elif member.isreg():
                    total += member.size
                    if total > max_total:
                        raise Failure("too_big_unpacked", link=shown, limit_gb=gb(max_total))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = tar.extractfile(member)
                    with open(target, "wb") as out:
                        shutil.copyfileobj(source, out, CHUNK)
                    os.chmod(target, 0o644)
                else:
                    raise Failure("archive_unsafe")
    except (tarfile.TarError, EOFError, zlib.error, OSError) as exc:
        if isinstance(exc, OSError) and exc.errno == 28:  # no space left
            raise Failure("too_big_unpacked", link=shown, limit_gb=gb(max_total))
        raise Failure("archive_unreadable", link=shown)
    for path in dest.rglob("*"):   # a directory the archive named without a member
        if path.is_dir():
            os.chmod(path, 0o755)
    return total


def read_small_json(path: Path) -> Any:
    with open(path, "rb") as handle:
        data = handle.read(MAX_MANIFEST_BYTES + 1)
    if len(data) > MAX_MANIFEST_BYTES:
        raise ValueError("too large")
    return json.loads(data)


def check_bundle_layout(bundle: Path, run: dict, plan: dict) -> None:
    if not ((bundle / "tool.json").is_file() and (bundle / "cases.jsonl").is_file()):
        raise Failure("bundle_layout")
    try:
        tool = read_small_json(bundle / "tool.json")
        found = {
            "program": tool["program"]["id"], "repeat": tool["repeat"], "track": tool["track"],
            "tool name": tool["tool"]["name"], "tool version": tool["tool"]["version"],
        }
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        raise Failure("bundle_invalid", errors=["tool.json cannot be read"])
    wanted = {"program": run["program"], "repeat": run["repeat"], "track": plan["track"],
              "tool name": plan["tool"]["name"], "tool version": plan["tool"]["version"]}
    different = [k for k in wanted if found[k] != wanted[k]]
    if different:
        raise Failure("bundle_mismatch", fields=different)


# ---------------------------------------------------------------------------------------------------------------------
# The container
# ---------------------------------------------------------------------------------------------------------------------

class Docker:
    """Runs docker with its output in a file, never in the log. Tests replace it."""

    def run(self, args: list[str], timeout: float, log: Path, name: str | None = None) -> int | None:
        """The exit code, or None when the time ran out (the container ``name`` is then killed)."""
        with open(log, "ab") as handle:
            try:
                return subprocess.run(["docker", *args], stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                      timeout=max(1.0, timeout), check=False).returncode
            except subprocess.TimeoutExpired:
                if name:
                    subprocess.run(["docker", "kill", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                return None


def container_args(image: str, runner_dir: Path, bundle: Path, out_parent: Path, name: str, command: list[str]) -> list[str]:
    """The documented flags of docs/SUBMITTING.md: no network, read-only root, own process view (Docker's default)."""
    return [
        "run", "--rm", "--name", name, "--platform", "linux/amd64", "--network", "none", "--read-only",
        "--pids-limit", "4096", "--memory", "14g", "--memory-swap", "14g", "--tmpfs", "/tmp:exec,size=2g",
        "-e", "PYTHONPATH=/cr/src", "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-v", f"{runner_dir}:/cr:ro", "-v", f"{bundle}:/bundle:ro", "-v", f"{out_parent}:/results",
        image, "python3", "-m", "agent_software_testing", *command,
    ]


def log_line(text: str) -> None:
    print(f"[replay] {text}", flush=True)


def redact_replay_json(doc: dict) -> dict:
    """Free text of the runner and case ids are not published. Counts and the rest stay as the runner wrote them."""
    if "setup_error_example" in doc:
        doc["setup_error_example"] = None
    timeouts = doc.get("timeouts")
    if isinstance(timeouts, dict) and "under_load_case_ids" in timeouts:
        timeouts["under_load_case_ids"] = []
    return doc


def public_counts(doc: dict) -> dict:
    """Copy the members of signatures-counts.json that hold counts and kinds; nothing else."""
    kinds: dict[str, dict[str, int]] = {}
    for sanitiser, by_kind in (doc.get("by_sanitizer_and_kind") or {}).items():
        if not (isinstance(sanitiser, str) and re.fullmatch(r"[a-z]{2,12}", sanitiser) and isinstance(by_kind, dict)):
            raise ValueError("kinds")
        kinds[sanitiser] = {}
        for kind, count in by_kind.items():
            if not (isinstance(kind, str) and re.fullmatch(r"[a-z0-9-]{1,40}", kind) and isinstance(count, int)
                    and not isinstance(count, bool) and count >= 0):
                raise ValueError("kinds")
            kinds[sanitiser][kind] = count
    signatures = doc["signatures"]
    if not isinstance(signatures, int) or isinstance(signatures, bool) or signatures < 0:
        raise ValueError("signatures")
    out = {"schema": doc.get("schema"), "program": doc.get("program"), "repeat": doc.get("repeat"),
           "signatures": signatures, "by_sanitizer_and_kind": kinds}
    if isinstance(doc.get("cases"), dict):
        out["cases"] = {k: v for k, v in doc["cases"].items() if isinstance(v, int) and not isinstance(v, bool)}
    if isinstance(doc.get("status_counts"), dict):
        out["status_counts"] = {k: v for k, v in doc["status_counts"].items() if isinstance(v, int) and not isinstance(v, bool)}
    return out


def write_json(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def remove_tree(path: Path, within: Path) -> None:
    """Remove a folder this script made inside its own work folder on the throwaway runner. Refuses anything else."""
    base = within.resolve()
    target = path.resolve()
    if not str(target).startswith(str(base) + os.sep):
        raise ValueError("refusing to remove a folder outside the work folder")
    if path.exists() and not path.is_symlink():
        shutil.rmtree(path)


def secure_open(path: Path):
    """Open a regular file for reading. A symbolic link or any other kind of file is an error."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file")
    except BaseException:
        os.close(fd)
        raise
    return os.fdopen(fd, "rb")


def read_output_json(path: Path) -> Any:
    with secure_open(path) as handle:
        data = handle.read(MAX_MANIFEST_BYTES + 1)
    if len(data) > MAX_MANIFEST_BYTES:
        raise ValueError("too large")
    return json.loads(data)


def copy_output_file(source: Path, target: Path) -> None:
    with secure_open(source) as src, open(target, "wb") as dst:
        shutil.copyfileobj(src, dst, CHUNK)


def is_real_dir(path: Path) -> bool:
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def check_output_folder(folder: Path, strict: bool = False) -> None:
    """The container can write the output mount, so a test case could plant a link there. Before the host reads or
    copies anything the folder must be a real directory and, when ``strict``, every entry a regular file (a folder
    called ``private`` may hold regular files). Raises OSError otherwise."""
    if not is_real_dir(folder):
        raise OSError("the output folder is not a real directory")
    if not strict:
        return
    with os.scandir(folder) as entries:
        for entry in entries:
            mode = entry.stat(follow_symlinks=False).st_mode
            if stat.S_ISREG(mode):
                continue
            if stat.S_ISDIR(mode) and entry.name == "private":
                with os.scandir(entry.path) as inner:
                    if not all(stat.S_ISREG(e.stat(follow_symlinks=False).st_mode) for e in inner):
                        raise OSError("unexpected entry")
                continue
            raise OSError("unexpected entry")


def start_container_work(stage: str, image: str, work: Path, docker: Any, remaining: Callable, sleep: Callable) -> tuple[Path, Path]:
    """Make the work folders and pull the image. Returns (logs, out_parent)."""
    if not IMAGE_RE.match(image):
        raise Failure("image_unavailable")
    work.mkdir(parents=True, exist_ok=True)
    logs = work / "private-logs"
    logs.mkdir(exist_ok=True)
    out_parent = work / "out"
    out_parent.mkdir(exist_ok=True)
    os.chmod(out_parent, 0o777)    # the image runs as uid 10001
    for attempt in range(3):
        if docker.run(["pull", "--quiet", image], remaining(), logs / "pull.log") == 0:
            return logs, out_parent
        sleep(10 * (attempt + 1))
    raise Failure("image_unavailable")


def fetch_bundle(stage: str, run: dict, plan: dict, work: Path, net: Any, sleep: Callable, free_bytes: int | None) -> Path:
    """Download, check and unpack the bundle of one run into ``work/bundle``; the archive is removed at once."""
    bundle = work / "bundle"
    free = free_bytes if free_bytes is not None else shutil.disk_usage(work).free
    limit = max(0, free - RESERVE_BYTES)
    link = resolve_link(run["source"], net, sleep)
    archive = work / "archive.tar"
    try:
        size = download(link, run["source"], archive, run["sha256"], limit // 2, net, sleep)
        log_line(f"{stage} {run['name']}: downloaded {gb(size)} GB, checksum ok")
        extract(archive, bundle, limit - size, run["source"])
    finally:
        try:
            archive.unlink()
        except OSError:
            pass
    check_bundle_layout(bundle, run, plan)
    return bundle


def run_attempts(stage: str, run: dict, bundle: Path, image: str, runner_dir: Path, logs: Path, out_parent: Path,
                 docker: Any, remaining: Callable, result: dict) -> Path:
    """The documented container command, with one retry. Returns the output folder of the attempt that worked."""
    program_json = f"/cr/programs/{run['program']}/program.json"
    command = {
        "coverage": ["replay", "/bundle", "--program", program_json, "--build",
                     f"/opt/agent-software-testing/builds/{run['program']}/gcov", "--quiet"],
        "signatures": ["signatures", "/bundle", "--program", program_json, "--build",
                       f"/opt/agent-software-testing/builds/{run['program']}/asan-ubsan"],
    }[stage]
    for attempt in range(1, REPLAY_ATTEMPTS + 1):
        result["attempts"] = attempt
        name = f"cr-{stage}-{attempt}"
        out_name = f"{run['name']}-a{attempt}"
        code = docker.run(container_args(image, runner_dir, bundle, out_parent, name,
                                         [*command, "--out", f"/results/{out_name}"]), remaining(), logs / "docker.log", name)
        if code is None:
            raise Failure("job_limit")
        if code == 0 and is_real_dir(out_parent / out_name):
            return out_parent / out_name
        log_line(f"{stage} {run['name']}: attempt {attempt} ended with exit code {code}")
    raise Failure("replay_failed" if stage == "coverage" else "signatures_failed")


def run_stage(stage: str, run: dict, plan: dict, *, image: str, runner_dir: Path, work: Path, result_dir: Path,
              data_dir: Path, budget_s: float = DEFAULT_BUDGET_S, net: Any = None, docker: Any = None,
              clock: Callable = time.monotonic, sleep: Callable = time.sleep, free_bytes: int | None = None) -> dict:
    """Coverage replay of one bundle. Always returns a result; never raises for a problem that has a public message."""
    net = net or Net()
    docker = docker or Docker()
    start = clock()
    result: dict = {"stage": stage, "name": run["name"], "program": run["program"], "repeat": run["repeat"],
                    "state": STATE_FAILED, "code": None, "attempts": 0}

    def remaining() -> float:
        return budget_s - (clock() - start)

    try:
        check_run_entry(run)
        logs, out_parent = start_container_work(stage, image, work, docker, remaining, sleep)
        log_line(f"{stage} {run['name']}: image ready")
        bundle = fetch_bundle(stage, run, plan, work, net, sleep, free_bytes)
        program_json = f"/cr/programs/{run['program']}/program.json"
        code = docker.run(container_args(image, runner_dir, bundle, out_parent, "cr-validate", [
            "validate", "/bundle", "--program", program_json]), remaining(), logs / "validate.log", "cr-validate")
        if code is None:
            raise Failure("job_limit")
        if code == 1:
            lines = [ln[7:] for ln in (logs / "validate.log").read_text(errors="replace").splitlines() if ln.startswith("error: ")]
            raise Failure("bundle_invalid", errors=lines[:5] or ["the validator rejected the bundle"])
        if code != 0:
            raise Failure("replay_failed")
        log_line(f"{stage} {run['name']}: bundle valid")
        output = run_attempts(stage, run, bundle, image, runner_dir, logs, out_parent, docker, remaining, result)

        data_dir.mkdir(parents=True, exist_ok=True)
        check_output_folder(output)
        summary = read_output_json(output / "replay.json")
        covered, total = summary["branch_covered"], summary["branch_total"]
        flake = summary["flake"]
        if (summary["program"]["id"] != run["program"] or summary["repeat"] != run["repeat"]
                or not all(isinstance(x, int) and not isinstance(x, bool) for x in (covered, total))):
            raise ValueError("outputs")
        write_json(data_dir / "replay.json", redact_replay_json(summary))
        copy_output_file(output / "coverage-branches.json", data_dir / "coverage-branches.json")
        copy_output_file(output / "timeline.csv", data_dir / "timeline.csv")
        result.update(covered=covered, total=total, cases=summary["cases"]["replayed"],
                      flake=flake["branches_differing"] if flake.get("checked") else None,
                      signals=summary["status_counts"]["terminated_by_signal"],
                      timeouts=summary["status_counts"]["timeout"],
                      not_started=summary["status_counts"].get("setup_error", 0))
        result.update(state=STATE_PASSED, code=None)
        log_line(f"{stage} {run['name']}: passed")
    except Failure as exc:
        result.update(state=STATE_FAILED, code=exc.code, params=exc.params)
        log_line(f"{stage} {run['name']}: failed ({exc.code})")
    except (ValueError, KeyError, TypeError, OSError, RecursionError):
        result.update(state=STATE_FAILED, code="replay_failed")
        log_line(f"{stage} {run['name']}: failed (the outputs could not be read)")
    write_json(result_dir / "result.json", result)
    return result


SIGNATURE_COLUMNS = ["system_id", "program", "sanitizer", "kind", "signatures", "runs"]


def run_signatures_program(program: str, runs: list[dict], plan: dict, *, image: str, runner_dir: Path, work: Path,
                           result_dir: Path, data_dir: Path, budget_s: float = DEFAULT_BUDGET_S, net: Any = None,
                           docker: Any = None, clock: Callable = time.monotonic, sleep: Callable = time.sleep,
                           free_bytes: int | None = None) -> dict:
    """The sanitiser pass over all repeats of one program, one repeat after the other, then the rows of
    derived/signatures.csv (a signature found in several repeats counts once). The details of the signatures stay in
    the work folder of the runner; only counts, kinds and rows are written to ``data_dir``."""
    import add_signatures   # the maintainers' own script, from the default branch

    net = net or Net()
    docker = docker or Docker()
    start = clock()
    result: dict = {"stage": "signatures", "name": program, "program": program, "state": STATE_FAILED, "code": None,
                    "repeats": []}

    def remaining() -> float:
        return budget_s - (clock() - start)

    try:
        if not NAME_RE.match(program) or not runs:
            raise Failure("signatures_failed")
        for run in runs:
            check_run_entry(run)
            if run["program"] != program:
                raise Failure("signatures_failed")
        runs = sorted(runs, key=lambda r: r["repeat"])
        logs, out_parent = start_container_work("signatures", image, work, docker, remaining, sleep)
        data_dir.mkdir(parents=True, exist_ok=True)
        log_line(f"signatures {program}: image ready, {len(runs)} repeats")
        folders: list[Path] = []
        stopped = False
        for run in runs:
            entry: dict = {"repeat": run["repeat"], "state": STATE_FAILED, "code": None, "attempts": 0}
            result["repeats"].append(entry)
            if stopped:
                entry["code"] = "job_limit"
                continue
            try:
                bundle = fetch_bundle("signatures", run, plan, work, net, sleep, free_bytes)
                output = run_attempts("signatures", run, bundle, image, runner_dir, logs, out_parent, docker, remaining, entry)
                check_output_folder(output, strict=True)
                doc = read_output_json(output / "signatures-counts.json")
                if doc.get("program") != program or doc.get("repeat") != run["repeat"]:
                    raise ValueError("outputs")
                counts = public_counts(doc)
                write_json(data_dir / f"signatures-counts-r{run['repeat']}.json", counts)
                entry.update(state=STATE_PASSED, signatures=counts["signatures"], kinds=counts["by_sanitizer_and_kind"])
                folders.append(output)
                log_line(f"signatures {program} r{run['repeat']}: passed")
            except Failure as exc:
                entry.update(code=exc.code, params=exc.params)
                stopped = exc.code == "job_limit"
                log_line(f"signatures {program} r{run['repeat']}: failed ({exc.code})")
            except (ValueError, KeyError, TypeError, OSError, RecursionError):
                entry.update(code="signatures_failed")
                log_line(f"signatures {program} r{run['repeat']}: failed (the outputs could not be read)")
            finally:
                remove_tree(work / "bundle", work)    # the disk stays bounded: one bundle at a time

        failed = [e for e in result["repeats"] if e["state"] != STATE_PASSED]
        if failed:
            result.update(code=failed[0]["code"], params=failed[0].get("params", {}))
        else:
            # Every folder was checked before it was read. The private files are read here, on the runner, and
            # nowhere else; they are not copied.
            for folder in folders:
                check_output_folder(folder, strict=True)
                private = folder / "signatures-private.json"
                if private.exists():
                    for sig in read_output_json(private).get("signatures", []):
                        if sig.get("kind") not in add_signatures.KIND_SPELLING:
                            raise Failure("signature_kind_unknown")
            try:
                rows = add_signatures.rows_for(plan["system_id"], program, folders)
            except SystemExit:
                raise Failure("signatures_failed")
            with open(data_dir / "signature-rows.csv", "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle, lineterminator="\n")
                writer.writerow(SIGNATURE_COLUMNS)
                writer.writerows(rows)
            result.update(state=STATE_PASSED, code=None,
                          rows=[{"sanitizer": r[2], "kind": r[3], "signatures": r[4]} for r in rows])
            log_line(f"signatures {program}: {len(rows)} rows")
    except Failure as exc:
        result.update(state=STATE_FAILED, code=exc.code, params=exc.params)
        log_line(f"signatures {program}: failed ({exc.code})")
    except (ValueError, KeyError, TypeError, OSError, RecursionError):
        result.update(state=STATE_FAILED, code="signatures_failed")
        log_line(f"signatures {program}: failed (the outputs could not be read)")
    finally:
        for private in out_parent_privates(work):
            try:
                private.unlink()     # crash details end here; the runner is discarded after the job anyway
            except OSError:
                pass
    write_json(result_dir / "result.json", result)
    return result


def out_parent_privates(work: Path) -> list[Path]:
    out = work / "out"
    found: list[Path] = []
    if is_real_dir(out):
        for folder in out.iterdir():
            if is_real_dir(folder):
                found += [p for p in (folder / "signatures-private.json", folder / "private" / "signatures-private.json")
                          if p.is_file() and not p.is_symlink()]
    return found



# ---------------------------------------------------------------------------------------------------------------------
# The summary comment
# ---------------------------------------------------------------------------------------------------------------------

def load_result(path: Path, stage: str, name: str) -> dict:
    """A result file from an artifact, which is untrusted. Only known members of the expected shape are kept."""
    try:
        raw = read_small_json(path)
    except (OSError, ValueError, RecursionError):
        return {"state": "lost", "code": "lost"}
    if not isinstance(raw, dict) or raw.get("stage") != stage or raw.get("name") != name:
        return {"state": "lost", "code": "lost"}

    def small_int(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 10 ** 12

    def safe_code(value: Any, state: str) -> str | None:
        return value if isinstance(value, str) and value in MESSAGES else ("lost" if state == STATE_FAILED else None)

    def safe_params(value: Any) -> dict:
        return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, (str, int, list))} \
            if isinstance(value, dict) else {}

    out: dict = {"state": STATE_PASSED if raw.get("state") == STATE_PASSED else STATE_FAILED}
    out["code"] = safe_code(raw.get("code"), out["state"])
    for key in ("covered", "total", "cases", "timeouts", "not_started", "flake", "attempts"):
        if small_int(raw.get(key)):
            out[key] = raw[key]
    out["params"] = safe_params(raw.get("params"))
    repeats = []
    for entry in raw.get("repeats") if isinstance(raw.get("repeats"), list) else []:
        if isinstance(entry, dict) and small_int(entry.get("repeat")):
            state = STATE_PASSED if entry.get("state") == STATE_PASSED else STATE_FAILED
            repeats.append({"repeat": entry["repeat"], "state": state, "code": safe_code(entry.get("code"), state),
                            "params": safe_params(entry.get("params"))})
    out["repeats"] = repeats
    rows = []
    for row in raw.get("rows") if isinstance(raw.get("rows"), list) else []:
        if isinstance(row, dict) and isinstance(row.get("sanitizer"), str) and isinstance(row.get("kind"), str) \
                and small_int(row.get("signatures")):
            rows.append({"sanitizer": clean(row["sanitizer"], 12), "kind": clean(row["kind"], 60),
                         "signatures": row["signatures"]})
    out["rows"] = rows
    return out


def findings_text(rows: list[dict]) -> str:
    parts = [f"{r['sanitizer']} {r['kind']} {r['signatures']}" for r in rows if r["kind"] != "none" and r["signatures"]]
    return ", ".join(parts) if parts else "none"


def cases_text(cov: dict) -> str:
    """How many cases were replayed, and how many of them gave no full measurement."""
    parts = [f"{cov[key]} {label}" for key, label in (("timeouts", "timed out"), ("not_started", "did not start"))
             if cov.get(key)]
    return f"{cov.get('cases', '?')}" + (f" ({', '.join(parts)})" if parts else "")


def build_comment(prepare_record: dict | None, results: dict, needs: dict, run_url: str,
                  commit: str = "") -> tuple[str, bool]:
    """The text of the comment and whether the whole check passed. ``results`` maps ("coverage", run name) and
    ("signatures", program) to a loaded result, or to None when the stage did not start."""
    lines = [MARKER]
    system = clean((prepare_record or {}).get("system_id") or "", 100)
    lines.append(f"## Replay of {('`submissions/' + system + '.json`') if system else 'this submission'}")
    lines.append("")
    if re.fullmatch(r"[0-9a-f]{40}", commit):
        lines += [f"For commit `{commit[:12]}`.", ""]
    problems = (prepare_record or {}).get("problems") or ([] if prepare_record else [{"code": "lost"}])
    if prepare_record is None or not prepare_record.get("ok"):
        lines.append("**Result: not replayed.** The manifest or the replay setup needs attention.")
        lines.append("")
        for problem in problems:
            lines.append(f"- {message(str(problem.get('code')), problem)}")
        lines.append("")
        lines.append(f"[Workflow run]({run_url})")
        return "\n".join(lines), False

    runs = prepare_record["runs"]
    programs = prepare_record.get("programs") or sorted({r["program"] for r in runs})
    approved = needs.get("approve") == "success"
    coverage = [results.get(("coverage", r["name"])) for r in runs]
    sanitiser = [results.get(("signatures", p)) for p in programs]
    cov_ok = sum(1 for c in coverage if c and c["state"] == STATE_PASSED)
    san_ok = sum(1 for s in sanitiser if s and s["state"] == STATE_PASSED)
    all_ok = approved and cov_ok == len(runs) and san_ok == len(programs)
    cancelled = needs.get("coverage") == "cancelled" or needs.get("sanitiser") == "cancelled"

    if not approved:
        lines.append("**Result: not run.** A maintainer has not approved the replay, or rejected it.")
    elif all_ok:
        lines.append(f"**Result: passed.** All {len(runs)} runs passed the coverage replay and all {len(programs)} "
                     "programs passed the sanitiser pass.")
    else:
        lines.append(f"**Result: failed.** Coverage replay: {cov_ok} of {len(runs)} runs passed. "
                     f"Sanitiser pass: {san_ok} of {len(programs)} programs. The results of the runs that passed are "
                     "kept as artifacts of the workflow run.")
        if cancelled:
            lines.append("Part of the check was cancelled.")
    lines += ["", "| Run | Coverage replay | Branches covered | Cases replayed | Flake check |", "|---|---|---|---|---|"]
    explain: list[str] = []
    coverage_stage_ok = approved and cov_ok == len(runs)
    for run, cov in zip(runs, coverage):
        name = clean(f"{run['program']} r{run['repeat']}", 60)
        if not approved:
            state, covered_text, cases_shown, flake_text = "not run", "", "", ""
        elif cov is None or cov["state"] != STATE_PASSED:
            state, covered_text, cases_shown, flake_text = "failed", "", "", ""
        else:
            state = "passed"
            covered_text = f"{cov.get('covered', '?')} / {cov.get('total', '?')}"
            cases_shown = cases_text(cov)
            flake_text = "not checked" if cov.get("flake") is None else f"{cov['flake']} branches differ"
        lines.append(f"| {name} | {state} | {covered_text} | {cases_shown} | {flake_text} |")
        if cov and cov["state"] != STATE_PASSED:
            explain.append(f"- **{name}, coverage replay.** {message(cov['code'] or 'lost', cov.get('params'))}")
    lines += ["", "| Program | Repeats counted | Sanitiser pass | Findings (signatures counted once over the repeats) |",
              "|---|---|---|---|"]
    for program, san in zip(programs, sanitiser):
        label = clean(program, 60)
        total = sum(1 for r in runs if r["program"] == program)
        if not coverage_stage_ok:
            lines.append(f"| {label} | | not started | |")
        elif san is None or san["state"] != STATE_PASSED:
            done = sum(1 for e in (san or {}).get("repeats", []) if e["state"] == STATE_PASSED)
            lines.append(f"| {label} | {done} of {total} | failed | |")
            entries = [e for e in (san or {}).get("repeats", []) if e["state"] != STATE_PASSED]
            if entries:
                for entry in entries:
                    explain.append(f"- **{label} r{entry['repeat']}, sanitiser pass.** "
                                   f"{message(entry['code'] or 'lost', entry.get('params'))}")
            else:
                explain.append(f"- **{label}, sanitiser pass.** {message((san or {}).get('code') or 'lost', (san or {}).get('params'))}")
        else:
            lines.append(f"| {label} | {total} of {total} | passed | {findings_text(san['rows'])} |")
    if explain:
        lines += ["", "### What failed and what to do", ""] + explain
    if not coverage_stage_ok and approved:
        lines += ["", message("skipped_stage")]
    incomplete = (prepare_record.get("incomplete") or [])
    if incomplete:
        lines += ["", "Programs with fewer than three repeats are kept but marked incomplete: "
                  + ", ".join(clean(p, 40) for p in incomplete) + "."]
    lines += ["", f"[Workflow run and artifacts]({run_url})"]
    text = "\n".join(lines)
    if len(text) > 60000:
        text = text[:59000] + "\n\n(The rest is cut. See the workflow run.)"
    return text, all_ok


def post_comment(api: Callable, repo: str, pr: int, body: str) -> str:
    """Update the comment that carries the marker, or create it. Returns 'updated' or 'created'."""
    page = 1
    while page <= 20:
        status, data = api_retry(api, "GET", f"/repos/{repo}/issues/{pr}/comments?per_page=100&page={page}")
        if status != 200:
            break
        comments = json.loads(data)
        for comment in comments:
            if (isinstance(comment, dict) and str(comment.get("body", "")).startswith(MARKER)
                    and (comment.get("user") or {}).get("type") == "Bot"):
                status, _data = api_retry(api, "PATCH", f"/repos/{repo}/issues/comments/{int(comment['id'])}", body={"body": body})
                if status >= 300:
                    raise OSError(f"GitHub refused the comment (status {status})")
                return "updated"
        if len(comments) < 100:
            break
        page += 1
    status, _data = api_retry(api, "POST", f"/repos/{repo}/issues/{pr}/comments", body={"body": body})
    if status >= 300:
        raise OSError(f"GitHub refused the comment (status {status})")
    return "created"


# ---------------------------------------------------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------------------------------------------------

def set_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def env_int(name: str, default: int | None = None) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        if default is None:
            raise SystemExit(f"{name} is missing or not a number")
        return default


def cmd_pins() -> int:
    try:
        pins = read_pins()
    except Failure as exc:
        print(message(exc.code))
        return 1
    for key, value in pins.items():
        set_output(key, value)
    return 0


def cmd_prepare() -> int:
    out = Path(os.environ["OUT_DIR"])
    record = prepare(github_api(os.environ["GH_TOKEN"], os.environ.get("GITHUB_API_URL", "https://api.github.com")),
                     os.environ["REPO"], env_int("PR_NUMBER"), os.environ["HEAD_SHA"], os.environ["HEAD_REPO"])
    plan = record.pop("plan", None)
    write_json(out / "prepare.json", record)
    if plan:
        write_json(out / "plan.json", plan)
    matrix = [{"index": r["index"], "name": r["name"]} for r in record["runs"]]
    set_output("matrix", json.dumps(matrix, separators=(",", ":")))
    set_output("programs", json.dumps([{"name": p} for p in record["programs"]], separators=(",", ":")))
    set_output("ok", "true" if record["ok"] else "false")
    if record["ok"]:
        log_line(f"manifest valid, {len(matrix)} runs planned")
        return 0
    for problem in record["problems"]:
        log_line(f"problem: {problem.get('code')}")
    return 1


def cmd_run() -> int:
    stage = os.environ["STAGE"]
    if stage not in STAGES:
        raise SystemExit("STAGE must be coverage or signatures")
    plan = read_small_json(Path(os.environ["PLAN_FILE"]))
    runs = plan["runs"]
    common = dict(image=os.environ["IMAGE"], runner_dir=Path(os.environ["CR_DIR"]).resolve(), work=Path(os.environ["WORK_DIR"]),
                  result_dir=Path(os.environ["RESULT_DIR"]), data_dir=Path(os.environ["DATA_DIR"]),
                  budget_s=env_int("JOB_BUDGET_S", DEFAULT_BUDGET_S))
    if stage == "signatures":
        program = os.environ["PROGRAM"]
        result = run_signatures_program(program, [r for r in runs if r.get("program") == program], plan, **common)
        return 0 if result["state"] == STATE_PASSED else 1
    index = env_int("RUN_INDEX")
    run = runs[index] if 0 <= index < len(runs) else {}
    result = run_stage(stage, run if run else {"name": "none", "program": "none", "repeat": 0}, plan, **common)
    return 0 if result["state"] == STATE_PASSED else 1


def cmd_finish() -> int:
    result_dir = Path(os.environ["RESULT_DIR"])
    if not (result_dir / "result.json").is_file():
        cancelled = os.environ.get("JOB_STATUS") == "cancelled"
        write_json(result_dir / "result.json", {"stage": os.environ["STAGE"], "name": os.environ["RUN_NAME"],
                                                "state": STATE_FAILED, "code": "cancelled" if cancelled else "lost"})
        log_line("no result was written; recorded as " + ("cancelled" if cancelled else "lost"))
    return 0


def cmd_comment() -> int:
    artifacts = Path(os.environ["ARTIFACT_DIR"])
    try:
        record = read_small_json(artifacts / "prepare" / "prepare.json")
        if not isinstance(record, dict):
            raise ValueError
        runs = []
        for r in record.get("runs") or []:
            check_run_entry({**r, "source": "https://example.org/x", "sha256": "0" * 64})
            runs.append({k: r[k] for k in ("index", "name", "program", "repeat")})
        record["runs"] = runs
        record["programs"] = sorted(p for p in (record.get("programs") or []) if isinstance(p, str) and NAME_RE.match(p))
        record["problems"] = [p for p in record.get("problems") or [] if isinstance(p, dict) and p.get("code") in MESSAGES]
    except (OSError, ValueError, KeyError, TypeError, Failure, RecursionError):
        record = None
    results = {}
    for run in (record or {}).get("runs") or []:
        results[("coverage", run["name"])] = load_result(
            artifacts / f"result-coverage-{run['name']}" / "result.json", "coverage", run["name"])
    for program in (record or {}).get("programs") or []:
        results[("signatures", program)] = load_result(
            artifacts / f"result-signatures-{program}" / "result.json", "signatures", program)
    needs = {k: os.environ.get(f"NEEDS_{k.upper()}", "") for k in ("prepare", "approve", "coverage", "sanitiser")}
    # a stage that did not run leaves no artifacts, which load_result reports as lost; show those as not started instead
    for key, stage in (("coverage", "coverage"), ("sanitiser", "signatures")):
        if needs[key] == "skipped":
            for name in ([r["name"] for r in (record or {}).get("runs") or []] if stage == "coverage"
                         else (record or {}).get("programs") or []):
                results[(stage, name)] = None
    body, ok = build_comment(record, results, needs, os.environ["RUN_URL"], os.environ.get("HEAD_SHA", ""))
    api = github_api(os.environ["GH_TOKEN"], os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    log_line("summary comment " + post_comment(api, os.environ["REPO"], env_int("PR_NUMBER"), body))
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    commands = {"pins": cmd_pins, "prepare": cmd_prepare, "run": cmd_run, "finish": cmd_finish, "comment": cmd_comment}
    if len(argv) != 2 or argv[1] not in commands:
        print(__doc__, file=sys.stderr)
        return 2
    return commands[argv[1]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
