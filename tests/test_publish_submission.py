"""scripts/publish_submission.py: a merged submission with a passed replay becomes a release, and nothing else does."""
import csv
import datetime
import gzip
import io
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import publish_submission as ps  # noqa: E402

REPO = "example-org/results"
HEAD = "c" * 40
TODAY = datetime.date(2026, 11, 20)
BULK = ("sets", "curves", "universe")


def copy_release(target: Path) -> Path:
    """A copy of this repository to write into. The bulk folders are linked file by file: new files go beside them."""
    def ignore(folder, names):
        return [n for n in names if n in (".git", "__pycache__", ".pytest_cache", *BULK)] if Path(folder) == ROOT else []
    shutil.copytree(ROOT, target, ignore=ignore)
    for top in BULK:
        shutil.copytree(ROOT / top, target / top, copy_function=os.link)
    return target


def zipped(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def universe_keys(program: str) -> list[str]:
    with gzip.open(ROOT / "universe" / f"{program}.txt.gz", "rb") as fh:
        return fh.read().decode().split("\n")[:-1]


def manifest(system: str, repeats=(1, 2, 3), **extra) -> dict:
    return {"schema": "agent-software-testing/submission@0.1", "system_id": system,
            "tool": {"name": "Newtool", "version": "1.2", "url": "https://example.org/newtool", "licence": "MIT", "family": "fuzzer", "model": None},
            "track": "open", "budget": {"wall_s": 3600, "cores": 2}, "hardware": {"cpu": "x", "memory_gib": 8, "gpu": None}, "tokens": None,
            "bundles": [{"program": "gzip", "repeat": r, "doi_or_url": f"https://example.org/r{r}.tar.gz", "sha256": "ab" * 32} for r in repeats],
            "contact_handle": "someone", "consent": {"publish_under_cc_by_4_0": True}, **extra}


class Replay:
    """What GitHub would hold for a finished replay. ``bad_key`` puts a key outside the universe into the first set."""

    def __init__(self, system="newtool", repeats=(1, 2, 3), head=HEAD, pr=7, conclusion="success", expired=False, bad_key=False, dip=0):
        self.head, self.pr, self.conclusion, self.expired = head, pr, conclusion, expired
        self.system = system
        keys = universe_keys("gzip")
        self.archives: dict[int, bytes] = {}
        self.listed = []
        plan = {"schema": "replay-plan@1", "pr": pr, "head_sha": head, "system_id": system, "track": "open",
                "tool": {"name": "Newtool", "version": "1.2"},
                "runs": [{"index": i, "name": f"gzip-r{r}", "program": "gzip", "repeat": r, "source": "https://example.org/x", "sha256": "ab" * 32}
                         for i, r in enumerate(repeats)]}
        self.add("prepare", {"plan.json": json.dumps(plan).encode()})
        for r in repeats:
            covered = sorted(keys[:100 + r] + (["no/such/file.c:1:0:0"] if bad_key and r == repeats[0] else []))
            n = len(covered)
            curve = [(0, 10), (900, 50 + dip), (1800, 50), (2700, n - 1), (3600, n)]
            self.add(f"data-coverage-gzip-r{r}", {
                "replay.json": json.dumps({"branch_covered": n, "branch_total": len(keys), "secret": "not copied"}).encode(),
                "coverage-branches.json": json.dumps({"schema": ps.SET_SCHEMA, "program": "gzip", "branch_key_format": "file:line:block_id:branch_id",
                                                      "branch_total": len(keys), "branch_covered": n, "covered_branch_keys": covered, "extra": "not copied"}).encode(),
                "timeline.csv": ("t_s,covered\n" + "".join(f"{t},{c}\n" for t, c in curve)).encode(),
            })
        rows = f"system_id,program,sanitizer,kind,signatures,runs\n{system},gzip,ASan,none,0,{len(repeats)}\n{system},gzip,UBSan,none,0,{len(repeats)}\n"
        self.add("data-signatures-gzip", {"signature-rows.csv": rows.encode()})

    def add(self, name: str, files: dict[str, bytes]) -> None:
        number = len(self.listed) + 100
        self.archives[number] = zipped(files)
        self.listed.append({"id": number, "name": name, "expired": self.expired})

    def api(self, method, path, body=None, raw=False, limit=None):
        assert method == "GET", "publishing only reads from GitHub"
        if "/pulls/7/files" in path:
            return 200, json.dumps([{"filename": f"submissions/{self.system}.json"}]).encode()
        if "/actions/workflows/replay.yml/runs" in path:
            assert f"head_sha={HEAD}" in path
            return 200, json.dumps({"workflow_runs": [{"id": 5, "conclusion": self.conclusion, "head_sha": self.head}]}).encode()
        if "/actions/runs/5/artifacts" in path:
            return 200, json.dumps({"artifacts": self.listed}).encode()
        raise AssertionError(path)

    def fetch(self, artifact_id):
        return self.archives[artifact_id]


def run(root: Path, replay: Replay, document: dict | None = None):
    document = document or manifest(replay.system)
    (root / "submissions" / f"{replay.system}.json").write_text(json.dumps(document), encoding="utf-8")
    return ps.publish(replay.api, replay.fetch, REPO, 7, HEAD, "2026-11-19", root=root, today=TODAY)


def table(root: Path, name: str) -> list[dict]:
    with open(root / name, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def published(tmp_path_factory):
    root = copy_release(tmp_path_factory.mktemp("release") / "copy")
    return root, run(root, Replay())


class TestPublished:
    def test_it_updates_the_data_without_a_new_version(self, published):
        root, result = published
        before = json.loads((ROOT / "RELEASE.json").read_text())
        release = json.loads((root / "RELEASE.json").read_text())
        assert result == "newtool"
        assert (release["release"], release["version"]) == (before["release"], before["version"])
        assert release["date"] == TODAY.isoformat()
        assert release["counts"]["systems"] == before["counts"]["systems"] + 1
        assert release["counts"]["runs"] == before["counts"]["runs"] + 3

    def test_the_new_tree_passes_the_full_check(self, published):
        root, _result = published
        ps.run_script(root, "check.py")

    def test_the_tables_hold_the_replay_and_only_the_replay(self, published):
        root, _result = published
        system = next(s for s in table(root, "systems.csv") if s["system_id"] == "newtool")
        assert (system["label"], system["family"], system["budget_s"], system["track"]) == ("Newtool 1.2, 2 cores, 1 h", "fuzzer", "3600", "open")
        assert (system["submitted"], system["status"]) == ("2026-11-19", "current")
        runs = [r for r in table(root, "runs.csv") if r["system_id"] == "newtool"]
        assert [(r["program"], r["repeat"], r["covered"]) for r in runs] == [("gzip", "1", "101"), ("gzip", "2", "102"), ("gzip", "3", "103")]
        with gzip.open(root / runs[0]["set_path"], "rb") as fh:
            stored = json.loads(fh.read())
        assert "extra" not in stored and stored["branch_covered"] == 101
        assert [t["tool_id"] for t in table(root, "tools.csv")].count("newtool") == 1
        assert [s for s in table(root, "derived/signatures.csv") if s["system_id"] == "newtool" and s["sanitizer"] == "ASan"][0]["kind"] == "none"


def write_replay_files(replay: Replay, folder: Path) -> Path:
    """The artifacts of a finished replay as the workflow downloads them, one folder for each."""
    by_id = {a["id"]: a["name"] for a in replay.listed}
    for number, archive in replay.archives.items():
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            zf.extractall(folder / by_id[number])
    return folder


class TestCandidate:
    def test_a_finished_replay_gives_a_candidate_that_passes_the_check(self, tmp_path):
        root = copy_release(tmp_path / "copy")
        before = (root / "RELEASE.json").read_text()
        replay = Replay()
        data = write_replay_files(replay, tmp_path / "data")
        plan = json.loads((data / "prepare" / "plan.json").read_text())
        plan["manifest"] = manifest("newtool")
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan))
        record = ps.candidate(plan_file, data, TODAY, root=root)
        assert record["ok"] is True, record
        assert (root / "submissions/newtool.json").is_file()
        assert "newtool" in [s["system_id"] for s in table(root, "systems.csv")]
        assert (ROOT / "RELEASE.json").read_text() == before

    def test_a_candidate_that_fails_the_check_says_so_and_does_not_raise(self, tmp_path):
        root = copy_release(tmp_path / "copy")
        replay = Replay(bad_key=True)
        data = write_replay_files(replay, tmp_path / "data")
        plan = json.loads((data / "prepare" / "plan.json").read_text())
        plan["manifest"] = manifest("newtool")
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps(plan))
        record = ps.candidate(plan_file, data, TODAY, root=root)
        assert record["ok"] is False and "check.py refused" in record["problem"]


@pytest.mark.parametrize("runs,best,words", [
    ([{"branch_covered": 0}], None, "cover no branch"),
    ([{"branch_covered": 100}, {"branch_covered": 100}, {"branch_covered": 100}], 50, "far above the best"),
    ([{"branch_covered": 10}, {"branch_covered": 100}, {"branch_covered": 110}], None, "differ a lot"),
    ([{"branch_covered": 100, "status_counts": {"ok": 5, "timeout": 5}}], None, "time limit"),
    ([{"branch_covered": 100, "flake": {"branches_differing": 50}}], None, "replayed again"),
])
def test_unusual_results_are_flagged(runs, best, words):
    assert any(words in flag for flag in ps.flags_for("gzip", runs, best))


def test_an_ordinary_result_is_not_flagged():
    runs = [{"branch_covered": n, "status_counts": {"ok": 30}, "flake": {"branches_differing": 0}} for n in (100, 105, 110)]
    assert ps.flags_for("gzip", runs, 200) == []


def test_withdrawing_an_entry_keeps_the_data_and_passes_the_check(tmp_path):
    root = copy_release(tmp_path / "copy")
    run(root, Replay())
    ps.withdraw(root, "newtool")
    assert next(s for s in table(root, "systems.csv") if s["system_id"] == "newtool")["status"] == "withdrawn"
    assert [r for r in table(root, "runs.csv") if r["system_id"] == "newtool"]
    with pytest.raises(ps.PublishError):
        ps.withdraw(root, "no-such-system")


def test_a_system_with_too_few_repeats_is_marked_incomplete_and_a_dip_is_stated(tmp_path):
    root = copy_release(tmp_path / "copy")
    run(root, Replay(repeats=(1,), dip=4), manifest("newtool", repeats=(1,)))
    row = [r for r in table(root, "runs.csv") if r["system_id"] == "newtool"][0]
    assert row["notes"] == "incomplete; curve dips by up to 4 branches"


@pytest.mark.parametrize("replay,words", [
    (Replay(conclusion="failure"), "no passed replay"),
    (Replay(head="d" * 40), "no passed replay"),
    (Replay(pr=8), "no passed replay"),
    (Replay(expired=True), "expired"),
])
def test_without_a_passed_replay_of_this_commit_nothing_changes(tmp_path, replay, words):
    root = copy_release(tmp_path / "copy")
    before = (root / "runs.csv").read_bytes()
    with pytest.raises(ps.PublishError, match=words):
        run(root, replay)
    assert (root / "runs.csv").read_bytes() == before


def test_the_full_check_stops_a_key_outside_the_universe(tmp_path):
    root = copy_release(tmp_path / "copy")
    with pytest.raises(ps.PublishError, match="check.py refused"):
        run(root, Replay(bad_key=True))


def test_a_replay_of_other_runs_is_refused(tmp_path):
    root = copy_release(tmp_path / "copy")
    with pytest.raises(ps.PublishError, match="other runs"):
        run(root, Replay(repeats=(1, 2)), manifest("newtool"))


def test_a_change_to_a_published_manifest_is_not_a_new_release(tmp_path):
    root = copy_release(tmp_path / "copy")
    system = table(root, "systems.csv")[0]["system_id"]
    replay = Replay(system=system)
    before = (root / "runs.csv").read_bytes()
    assert ps.publish(replay.api, replay.fetch, REPO, 7, HEAD, "2026-11-19", root=root, today=TODAY) is None
    assert (root / "runs.csv").read_bytes() == before


def test_text_from_a_manifest_cannot_write_markdown_or_html(tmp_path):
    root = copy_release(tmp_path / "copy")
    document = manifest("newtool", notes="[click](http://evil.example) <script>x</script> @someone `rm`")
    document["tool"]["name"] = "New<b>tool</b> | [x](y)"
    run(root, Replay(), document)
    system = next(s for s in table(root, "systems.csv") if s["system_id"] == "newtool")
    tool = next(t for t in table(root, "tools.csv") if t["tool_id"] == system["tool_id"])
    for text in (system["label"], system["description"], tool["name"], tool["description"]):
        for bad in ("<", ">", "[", "]", "@", "`", "|"):
            assert bad not in text


def test_a_tool_of_another_name_keeps_its_own_row(tmp_path):
    root = copy_release(tmp_path / "copy")
    with open(root / "tools.csv", "a", encoding="utf-8") as fh:
        fh.write("newtool,Something Else,,,Another tool\n")
    with open(root / "systems.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    rows.append([("newtool" if c == "tool_id" else "other-system" if c == "system_id" else v) for c, v in zip(rows[0], rows[1])])
    with open(root / "systems.csv", "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh, lineterminator="\n").writerows(rows)
    with pytest.raises(ps.PublishError):  # the copy's extra system has no runs in the derived tables; only the tool id matters here
        run(root, Replay())
    assert "newtool-newtool" in [t["tool_id"] for t in table(root, "tools.csv")]


def test_a_name_inside_an_artifact_is_only_looked_up():
    archive = zipped({"../../evil.txt": b"x", "plan.json": b"{}"})
    assert ps.read_member(archive, "plan.json") == b"{}"
    with pytest.raises(ps.PublishError):
        ps.read_member(archive, "replay.json")


def test_the_workflow_runs_only_after_a_merge_and_never_checks_out_the_pull_request():
    text = (ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
    assert "github.event.pull_request.merged == true" in text
    assert "ref: main" in text
    for line in text.splitlines():
        assert not (line.strip().startswith("ref:") and "head" in line)
    assert "pull_request_target" in text and "\n  pull_request:" not in text


def test_no_workflow_needs_a_stored_secret():
    for name in ("publish.yml", "replay.yml"):
        assert "secrets." not in (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
