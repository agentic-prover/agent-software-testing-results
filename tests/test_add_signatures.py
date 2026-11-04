"""scripts/add_signatures.py: counts of one system on one program from the sanitiser pass of each repeat."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import add_signatures  # noqa: E402


def output(folder, name="signatures-asan", signatures=()):
    folder.mkdir()
    (folder / f"{name}-counts.json").write_text(json.dumps({"signatures": len(signatures)}))
    if signatures:
        (folder / f"{name}-private.json").write_text(json.dumps({"signatures": [{"sanitizer": s, "kind": k, "frame": f} for s, k, f in signatures]}))
    return folder


def test_a_sanitiser_that_found_nothing_gets_one_none_row(tmp_path):
    folders = [output(tmp_path / f"r{i}") for i in (1, 2, 3)]
    assert add_signatures.rows_for("s", "p", folders) == [["s", "p", "ASan", "none", 0, 3]]


def test_a_signature_found_in_two_repeats_counts_once(tmp_path):
    one = ("asan", "double-free", "a.c:1")
    two = ("asan", "double-free", "b.c:2")
    three = ("asan", "double-free", "c.c:3")
    folders = [output(tmp_path / "r1", signatures=[one]), output(tmp_path / "r2", signatures=[one, two, three]), output(tmp_path / "r3")]
    assert add_signatures.rows_for("s", "p", folders) == [["s", "p", "ASan", "double-free", 3, 3]]


def test_a_pass_over_one_build_covers_both_sanitisers(tmp_path):
    folders = [output(tmp_path / "r1", name="signatures")]
    assert add_signatures.rows_for("s", "p", folders) == [["s", "p", "ASan", "none", 0, 1], ["s", "p", "UBSan", "none", 0, 1]]


def test_counts_without_their_private_file_stop(tmp_path):
    folder = output(tmp_path / "r1")
    (folder / "signatures-asan-counts.json").write_text(json.dumps({"signatures": 1}))
    with pytest.raises(SystemExit, match="private file is missing"):
        add_signatures.rows_for("s", "p", [folder])


def test_a_kind_without_a_published_spelling_stops(tmp_path):
    folder = output(tmp_path / "r1", signatures=[("asan", "invented-kind", "main")])
    with pytest.raises(SystemExit, match="unknown error kind"):
        add_signatures.rows_for("s", "p", [folder])


def test_a_folder_without_counts_stops(tmp_path):
    (tmp_path / "r1").mkdir()
    with pytest.raises(SystemExit, match="no sanitiser counts"):
        add_signatures.rows_for("s", "p", [tmp_path / "r1"])


def test_the_table_gets_kinds_and_counts_and_never_a_frame(tmp_path):
    """Run as the maintainers run it: the rows appended to derived/signatures.csv hold the kind and the count, and the
    frames that were in the private files appear nowhere; a system already in the table is not added again."""
    import shutil
    import subprocess

    root = tmp_path / "repo"
    (root / "derived").mkdir(parents=True)
    shutil.copytree(Path(add_signatures.__file__).parent, root / "scripts")
    (root / "derived" / "signatures.csv").write_text("system_id,program,sanitizer,kind,signatures,runs\n")
    frames = [("asan", "heap-buffer-overflow", "lib/secret_name.c:parse_secret"), ("asan", "heap-buffer-overflow", "lib/other.c:other_function")]
    folders = [output(tmp_path / "r1", signatures=frames), output(tmp_path / "r2", signatures=frames[:1])]
    command = [sys.executable, "-I", str(root / "scripts" / "add_signatures.py"), "sys", "prog", *map(str, folders)]
    done = subprocess.run(command, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    table = (root / "derived" / "signatures.csv").read_text()
    assert table.splitlines()[1:] == ["sys,prog,ASan,heap-buffer-overflow,2,2"]
    for private in ("secret_name", "parse_secret", "other_function", "lib/"):
        assert private not in table and private not in done.stdout
    again = subprocess.run(command, capture_output=True, text=True)
    assert again.returncode != 0 and (root / "derived" / "signatures.csv").read_text() == table


def test_every_kind_can_be_written_and_passes_the_release_check():
    """S1. What the scripts can write is what check.py accepts, or a submission would stop at the last step."""
    import check
    from import_replay import KIND_SPELLING

    assert set(KIND_SPELLING.values()) <= check.ERROR_CLASSES
    assert all(check.KIND_RE.match(spelled) for spelled in KIND_SPELLING.values())


def test_every_kind_the_replay_tool_can_report_has_a_spelling():
    """S1. A kind without a spelling fails a submission whose program shows that bug."""
    import os

    from import_replay import KIND_SPELLING

    root = Path(__file__).resolve().parent.parent
    runner = Path(os.environ.get("AGENT_SOFTWARE_TESTING_RUNNER", root.parent / "agent-software-testing"))
    source = runner / "src" / "agent_software_testing" / "kinds.py"
    if not source.is_file():
        pytest.skip("needs a checkout of the agent-software-testing repository (AGENT_SOFTWARE_TESTING_RUNNER)")
    namespace = {}
    exec(compile(source.read_text(encoding="utf-8"), str(source), "exec"), namespace)
    reported = set(namespace["ASAN_KINDS"]) | set(namespace["UBSAN_KINDS"]) | {namespace["OTHER_KIND"]}
    assert reported - set(KIND_SPELLING) == set()
