"""scripts/add_signatures.py --rows: the rows file of the replay workflow came through an artifact and is checked."""
import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import add_signatures  # noqa: E402

HEADER = ["system_id", "program", "sanitizer", "kind", "signatures", "runs"]


def write(path, rows, header=HEADER):
    with open(path, "w", newline="") as handle:
        csv.writer(handle, lineterminator="\n").writerows([header, *rows])
    return path


def setup(tmp_path, monkeypatch, existing=()):
    (tmp_path / "derived").mkdir()
    write(tmp_path / "derived" / "signatures.csv", existing)
    monkeypatch.setattr(add_signatures, "ROOT", tmp_path)


GOOD = [["s", "p", "ASan", "double-free", "2", "3"], ["s", "p", "UBSan", "none", "0", "3"]]


def test_good_rows_are_appended(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    rows = write(tmp_path / "rows.csv", GOOD)
    assert add_signatures.main(["s", "p", "--rows", str(rows)]) == 0
    text = (tmp_path / "derived" / "signatures.csv").read_text().splitlines()
    assert text[1:] == ["s,p,ASan,double-free,2,3", "s,p,UBSan,none,0,3"]


def test_existing_rows_for_the_system_and_program_are_refused(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, existing=[["s", "p", "ASan", "none", "0", "3"]])
    rows = write(tmp_path / "rows.csv", GOOD)
    with pytest.raises(SystemExit, match="already has"):
        add_signatures.main(["s", "p", "--rows", str(rows)])


@pytest.mark.parametrize("bad", [
    [["s", "p", "ASan", "made-up kind", "1", "3"]],          # kind not in KIND_SPELLING
    [["s", "p", "TSan", "none", "0", "3"]],                  # unknown sanitiser
    [["s", "p", "ASan", "double-free", "x", "3"]],           # not an integer
    [["s", "p", "ASan", "double-free", "-1", "3"]],
    [["s", "p", "ASan", "double-free", "1.5", "3"]],
    [["s", "p", "ASan", "none", "2", "3"]],                  # none with signatures
    [["s", "p", "ASan", "double-free", "0", "3"]],           # a kind with no signature
    [["s", "p", "ASan", "double-free", "1", "0"]],           # no run
    [["other", "p", "ASan", "double-free", "1", "3"]],       # another system
    [["s", "other", "ASan", "double-free", "1", "3"]],       # another program
    [["s", "p", "ASan", "double-free", "1", "3"], ["s", "p", "ASan", "double-free", "1", "3"]],
    [["s", "p", "ASan", "double-free", "1"]],                # short row
    [],                                                      # no rows
])
def test_bad_rows_are_refused_and_nothing_is_written(tmp_path, monkeypatch, bad):
    setup(tmp_path, monkeypatch)
    rows = write(tmp_path / "rows.csv", bad)
    with pytest.raises(SystemExit):
        add_signatures.main(["s", "p", "--rows", str(rows)])
    assert (tmp_path / "derived" / "signatures.csv").read_text().splitlines() == [",".join(HEADER)]


def test_wrong_columns_and_missing_file_are_refused(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    rows = write(tmp_path / "rows.csv", GOOD, header=["system_id", "program", "sanitizer", "kind", "frame", "runs"])
    with pytest.raises(SystemExit, match="columns"):
        add_signatures.main(["s", "p", "--rows", str(rows)])
    with pytest.raises(SystemExit):
        add_signatures.main(["s", "p", "--rows", str(tmp_path / "missing.csv")])


def test_rows_option_takes_exactly_one_file(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    assert add_signatures.main(["s", "p", "--rows"]) == 2
    assert add_signatures.main(["s", "p", "--rows", "a", "b"]) == 2
