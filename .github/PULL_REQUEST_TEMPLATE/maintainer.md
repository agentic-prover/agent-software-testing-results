## What this changes

<!-- One or two sentences. Link the issue for a correction (the Data issue). A submission uses the submission template. -->

## Kind of change

- [ ] New runs from an accepted submission (the submission pull request: #)
- [ ] Correction of the data (Data issue: #); the release that is wrong:
- [ ] Scripts
- [ ] Documentation
- [ ] Other:

## Checklist for every pull request

- [ ] `python scripts/derive.py` was run and its output is committed; no derived file was edited by hand.
- [ ] `python scripts/check.py` passes (with the `agent-software-testing` repository checked out next to this one).
- [ ] No published release was edited. A correction of a published number is an entry in the errata of the next release.
- [ ] No frames, function or file names, stack traces, inputs, case hashes or descriptions of causes of sanitiser
      findings, and no personal data, user or host names, email addresses or local paths.
- [ ] Data is licensed CC BY 4.0 and scripts Apache-2.0; I have the right to contribute them that way.

## Additional checklist for new runs

- [ ] Every bundle was downloaded, its SHA-256 matches the manifest, and it passes `python -m agent_software_testing validate`.
- [ ] Every bundle was replayed with the pinned image and the reference runner. Runner version and image identity:
- [ ] The flake check was run; the largest number of differing branches is:
- [ ] The sanitiser pass was run where the program allows it. Only counts by error class are published, for every
      program; locations, stack traces and inputs of bugs not yet fixed upstream are not.
- [ ] `submissions/<system_id>.json` is added and validates against `submissions/schema.json`.
- [ ] `systems.csv` has the system with its family, track, budget, cores, measure and group; programs with fewer than three
      repeats carry the note `incomplete`.
- [ ] The submitter reviewed this pull request. If a maintainer is an author of the tool, another maintainer approved it.
