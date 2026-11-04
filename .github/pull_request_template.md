## Submission

This pull request adds `submissions/<system_id>.json`. The file is the submission. Read `docs/SUBMITTING.md` first.

A maintainer approves each submission by hand before anything runs, which can take a few working days. A check reads
the manifest at once and says in a comment if something is wrong.

- [ ] Every bundle passes `python -m agent_software_testing validate` and its archive can be downloaded without logging in.
- [ ] The bundles contain the test cases my tool produced within the stated budget and configuration.
- [ ] I agree that the measurements derived from my bundles, with the tool's name, version and my self-reported resources, are published under CC BY 4.0.

Do not describe bugs you found in one of the programs here. Send them privately (`DISCLOSURE.md`).

<!-- Any other change: replace this text with what changes and why. Maintainers adding runs use `.github/PULL_REQUEST_TEMPLATE/maintainer.md`. -->
