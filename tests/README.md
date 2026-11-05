# Tests of the results repository

A release is wrong when a number in it cannot be traced to a branch set, when two tables disagree or when something
private is published. The tests here are the few that stop those things, each tied to a promise in the list below. A test
that cannot be tied to a promise does not belong here.

The tests run the scripts as a user does and look at the files they write. The data is the release in this repository, so
no test depends on a particular number in it. To see that `check.py` really refuses, a test copies the release, makes one
change the way a mistake or a bad actor would and runs the copy's own check.

## Running them

| Command | What it does |
|---|---|
| `python3 -m pytest tests` | The everyday run. One test is skipped when the `jsonschema` package is missing. One more is skipped when the runner is not checked out next to this repository. |
| `AGENT_SOFTWARE_TESTING_RUNNER=<checkout of the runner> python3 -m pytest tests` | The same. Every part that `package_inputs.py` writes is then also checked with the validator of the runner. |
| `python3 tests/break_it.py` | Breaks the scripts on purpose, one bug at a time in a throwaway copy. It says for each whether a test noticed. `--list` shows the bugs. |

`break_it.py` leaves its copies in the folder it names at the start and deletes nothing. Its bugs are in
`break_it_bugs.py`, each with the promise it breaks. Rows that start with `OK-` are harmless changes that the tests have
to survive.

## What must never break

### The release

| | Promise | Tests |
|---|---|---|
| R1 | The release in the repository passes its own check. | `test_the_release_in_this_repository_passes_its_own_check` |
| R2 | `check.py` refuses a release that is tampered, incomplete or inconsistent. The tests make each change listed after this table and expect a refusal. | `test_a_release_with_this_change_is_refused` (one case for each change) |
| R3 | What the release says about itself agrees with its tables. | `test_the_readme_states_the_counts_of_the_tables` |
| R4 | Nothing public holds a crash report, a frame or an input of a sanitiser finding. | `test_no_public_file_holds_a_crash_report`, `test_the_table_gets_kinds_and_counts_and_never_a_frame` and the signature rows of R2 |

Changes the R2 tests make, each on a copy of the release.

* A covered branch that is not in the program. A set with keys out of order. A set with a key twice. A set that counts more than it lists. A set that belongs to another program.
* A run whose covered count differs from its set. A set that lost a branch the run table still counts. A run whose set is that of another run. A set or curve file that is missing. A file in `sets/` that no run names.
* A universe with a branch more. A universe out of order. Another branch total for a program. Another total for a run.
* A curve that ends away from its run. A curve that falls. A curve that lacks a checkpoint.
* A final or an overlap row that no longer matches the runs. An overlap pair that is missing.
* A signature table with a location in a value. A signature table with a frame column. An error class that is not listed.
* A release record with wrong counts. A release record without the public flag. A release record without the image.
* A system that copies another. A system superseded by nothing. A run of an unknown system. A run listed twice. Runs out of order. A tool that no system uses.
* A systems table in the README that is out of date. A README without its line on what is not published.
* A required file that is missing. A script without its licence line. A submission manifest that breaks its schema.

### The tables people read

| | Promise | Tests |
|---|---|---|
| T1 | The median, minimum and maximum of a system on a program are worked out as the specification says. So is the overlap of two systems (repeats 1 to 3 only, complete pairs only, one universe). | `test_derive.py` (numbers worked out by hand) |
| T2 | `derive.py` rebuilds the published derived tables and the README table byte for byte. | `test_regenerating_the_derived_files...` |

### How results enter and leave

| | Promise | Tests |
|---|---|---|
| U1 | A merged submission is published only from a passed replay of exactly its last commit. | `test_without_a_passed_replay...`, `test_a_replay_of_other_runs...` |
| U2 | What it adds passes the full check, makes no new version, flags what a person should look at and states what a reader must know about a run. | `TestPublished`, `TestCandidate`, `test_the_full_check_stops...`, `test_a_system_with_too_few...`, `test_unusual_results_are_flagged` |
| U3 | Text from a manifest cannot write Markdown or HTML into the tables, and a name inside an artifact reaches nothing. | `test_text_from_a_manifest...`, `test_a_name_inside_an_artifact...` |
| U4 | A change to a manifest that is already published adds nothing, and a published name cannot be submitted again. | `test_a_change_to_a_published_manifest...`, `test_a_published_system_name_is_refused` |
| U6 | A pull request that changes anything beside its one manifest gets no replay, so it cannot be published with code in it. | `test_a_submission_may_change_nothing_but_its_manifest` |
| U5 | An entry can be withdrawn with the data kept. No workflow needs a stored secret, and none checks out the pull request. | `test_withdrawing_an_entry...`, `test_no_workflow_needs_a_stored_secret`, `test_the_workflow_runs_only_after_a_merge...` |
| I1 | An import gives back exactly the runs that were replayed. It carries nothing of the machine they ran on. The release it leaves passes the check. | `test_an_import_gives_back_exactly...` |
| I2 | Tables that disagree with each other or with the release are refused before a file is changed. | `test_tables_that_disagree...`, `test_a_file_that_changed_after...` |
| I3 | An import never writes over a release unless asked to. | `test_an_import_does_not_write_over...` |
| I4 | What is written is the same bytes every time. The cleanup stays inside the repository. | `test_the_sets_it_writes...`, `test_the_cleanup_never_removes...` |
| S1 | `add_signatures.py` counts one signature once however many repeats show it. It writes kinds and counts and never a frame. It does not add a system twice. | `test_add_signatures.py` |
| V1 | `validate_manifest.py` answers as the JSON Schema does for every single break of the example. Its exit status gives the same answer. | `test_validate_manifest.py` |
| K1 | `package_inputs.py` leaves out the programs whose inputs are held back whatever the case of the name. Its parts are bundles that together make the whole run. It never splits a case, keeps below the size limit, gives the same bytes every time and writes only into a new folder. | `test_package_inputs.py` |
| W | A stranger's pull request is data. `replay_job.py` keeps a hostile manifest, archive or result file off the runner's host side, downloads only from the public internet, publishes kinds and counts only, always gives the submitter a reason and shows how many cases were really measured. | `test_replay_job.py` |
