# The replay workflow

A pull request that adds `submissions/<system_id>.json` is a submission. The workflow in `.github/workflows/replay.yml`
reads the manifest, waits for a maintainer to approve, replays every listed bundle on free GitHub runners and posts one
comment on the pull request. It never pushes to the repository. Results enter the data only when a maintainer merges.

## Settings to change once

1. **Create the environment.** Open Settings, then Environments, then New environment. Name it `replay`. Under
   Required reviewers add the maintainers who may approve a replay. Leave the other options as they are. This is the
   only gate. The manifest job runs at once on every pull request. Everything that replays waits for this approval.
2. **Publish the replay image.** In the runner repository open Actions, choose "Build replay image" and run it. It
   compiles all 18 programs, so allow several hours. The run summary shows the image name with its digest and the
   size. Open the package in GitHub Container Registry and check that it is public, because the replay jobs hold no
   secrets and pull it anonymously.
3. **Pin the image and the runner.** Edit `.github/replay-pins.json` through a pull request. Set `image` to the line
   from the run summary and `runner_ref` to the full commit of the runner repository that should do the replay. Until
   both are set every submission fails at once with a message that the setup is not finished.

The workflow file and `scripts/replay_job.py` always come from the default branch. A pull request cannot change them
for its own run. Changes to either go through review like any other change.

## What happens to a submission

1. The pull request is opened or updated. The job "Read the manifest" runs at once. It reads the manifest through
   the GitHub API as data and checks it against `submissions/schema.json`. It also checks the program names, the
   repeats and the links. If something is wrong the comment says what to fix and nothing else runs.
2. If the manifest is fine the job "Wait for approval" stays pending. Read the manifest and the links. Then open the
   workflow run, choose Review deployments, tick `replay` and approve or reject. This can wait for days. The
   comment appears when the run ends, so a pending replay shows only as a pending check on the pull request.
3. After approval stage one starts. Each program and repeat is one job and 20 run at a time. Each job downloads its
   archive, checks the SHA-256, unpacks it safely, validates the bundle and replays it in the container with no
   network and a read-only root file system. The flake check runs as part of the replay.
4. Stage two, the sanitiser pass, starts only when every job of stage one has passed. It needs no second approval.
   It has one job per program. Each job takes the repeats of its program one after the other. It downloads and checks
   a bundle, runs the signature pass, removes the bundle and goes on with the next repeat. Afterwards it counts each
   signature once over the repeats, on the runner.
5. The last job writes or updates one comment with a table of every run and, for each failure, what failed and
   what to do. It fails when any run failed. The pull request then stays red.

A new push to the pull request cancels the running workflow and starts a new one that needs approval again.

## When something fails

| Message in the comment | What it means | What to do |
|---|---|---|
| The manifest or form details are wrong | The file does not follow the schema or a name or link is not allowed | Nothing for the maintainer. The submitter fixes the file and pushes. |
| Download failed (names the link) | Three tries failed | Check the link. If the host was down, re-run the failed jobs later. |
| Checksum mismatch | The file is not the one the manifest names | The submitter fixes the link or the hash. |
| Bundle invalid or does not match the manifest | The validator or the comparison with the manifest said no | The submitter fixes the bundle and the hash. |
| Too big for the disk | The archive or the unpacked bundle does not fit the free runner | The submitter makes the bundle smaller. |
| Reached the job limit | A run needed more than about 5 hours 40 minutes | The submitter sends fewer cases. A paid runner is a decision for the owners. |
| Runner error after two tries | The replay stopped with an error although the bundle is valid | Look at the run, then re-run the failed jobs. If it repeats, check the pinned runner and image. |
| Error kind not listed | The sanitiser pass found a kind that `KIND_SPELLING` does not list | Not the submitter's fault. Add the kind to the scripts, then close and reopen the pull request. |
| Lost or cancelled | The machine died, GitHub had an outage or the run was cancelled | Not the submitter's fault. Re-run the failed jobs. |

A test case that crashes the program is normal. It counts as a finding and the run still passes.

## How to re-run

Open the workflow run in the Actions tab and choose "Re-run failed jobs". Runs that passed keep their results. Their
artifacts stay and the new attempt replaces the artifacts of the runs that failed. The comment is updated by the last
job of the new attempt. GitHub may ask for approval of the environment again.

A re-run uses the scripts and the pins of the commit the run started from. After a change to the scripts or to
`.github/replay-pins.json`, close and reopen the pull request instead: that starts a new run, which needs approval again.

## What to take for ingest

Every run uploads artifacts to the workflow run, kept for 30 days.

* `data-coverage-<program>-r<N>` holds `replay.json`, `timeline.csv` and `coverage-branches.json`. Two free-text
  fields of `replay.json` (`setup_error_example` and the list of case ids under `timeouts`) are emptied before upload.
* `data-signatures-<program>` holds `signature-rows.csv` and one `signatures-counts-r<N>.json` for each repeat. The
  rows have the columns of `derived/signatures.csv` and already count each signature once over the repeats.

The details of the signatures (frames, examples and case ids) never leave the runner. For the sanitiser counts of a
program run

```
python3 scripts/add_signatures.py <system_id> <program> --rows signature-rows.csv
```

The script checks the columns, the sanitisers, the kinds and the counts of the file. It refuses when
`derived/signatures.csv` already has rows for that system and program. If a kind is missing from `KIND_SPELLING` the job
fails with a message that a maintainer must add the kind. Add it to `scripts/import_replay.py` and `scripts/check.py`, then close and reopen the pull request.

The `result-*` artifacts hold the state of each run and are what the comment is built from.

## Limits

* The free plan runs 20 jobs at a time. A manifest can list at most 256 bundles because a matrix holds no more.
* One job lasts at most 6 hours. The workflow stops a replay after 5 hours 40 minutes and reports it. A sanitiser job
  shares that time between all repeats of its program.
* The disk of a free runner is about 14 GB. The workflow measures the free space at run time and refuses a bundle
  that does not fit.
* DOIs work for Zenodo records that hold one file. Any other link must be a direct https address without
  ampersands, semicolons or spaces.
* Artifacts count against the storage of the account. They are kept for 30 days.

## Safety in short

* `pull_request_target` is used so that the workflow always comes from the default branch. Nothing from the pull
  request is checked out or run. The manifest is read as data.
* Values from the pull request are passed in environment variables and checked before use. No script text is built
  from them.
* The replay jobs hold no secrets and have read-only repository access. Only the last job can write to the pull request
  and it treats the artifacts as untrusted text.
* Logs and artifacts hold branch identities, counts and kinds only. Branch identities are what the release publishes
  in `sets/`. The program output of a replay is written to files that are never uploaded.
* The environment `replay` must have required reviewers before the pins are set. GitHub creates a missing environment
  without reviewers, and then nothing holds a replay back.
* A replay measures the bundle it was given. The program under test runs as the same user as the measurement, so a
  bundle can forge its coverage and its sanitiser counts (`SPEC.md` section 6.7). Read what a submission claims before
  you merge it.
