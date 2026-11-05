# The replay workflow

A pull request that adds `submissions/<system_id>.json` is a submission. The workflow in `.github/workflows/replay.yml`
reads the manifest, waits for a maintainer to approve, replays every listed bundle on free GitHub runners and posts one
comment on the pull request with a preview of the website. It never pushes to the repository. Results enter the data
only when a maintainer merges; `.github/workflows/publish.yml` then adds them and updates the website.

## Settings to change once

1. **Create the environment.** Open Settings, then Environments, then New environment. Name it `replay`. Under
   Required reviewers add the maintainers who may approve a replay. Leave the other options as they are. This is the
   only gate. The manifest job runs at once on every pull request. Everything that replays waits for this approval.
2. **Publish the replay image.** In the runner repository open Actions, choose "Build replay image" and run it. It
   compiles all 18 programs, so allow about an hour. The run summary shows the image name with its digest and the
   size. A new package comes out private. Open its settings and make it public, because the replay jobs hold no
   secrets and pull it anonymously.
3. **Pin the image and the runner.** Edit `.github/replay-pins.json`. Set `image` to the line
   from the run summary and `runner_ref` to the full commit of the runner repository that should do the replay. Until
   both are set every submission fails at once with a message that the setup is not finished.
4. **Serve the website from this repository.** Open Settings, then Pages. Set the source to Deploy from a branch with
   `main` and `/ (root)`, press Save, then set it to GitHub Actions, because the second setting alone does not create the
   site. The custom domain goes there too. No stored secret is needed: each job uses the short-lived permissions GitHub gives it.
5. **Allow the workflows' trigger.** From 2 December 2026 GitHub blocks `pull_request_target` in public repositories
   unless a policy allows it, and without it nothing starts when a pull request is opened. Open Settings, then under
   Actions choose Policies and create a policy for `.github/workflows/replay.yml` and `.github/workflows/publish.yml`
   that allows the events `pull_request_target` and `workflow_dispatch`, with its status active.

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
6. A preview follows when every run has passed. It adds the results to a copy of the data, runs the same checks as a
   merge and builds the website with them under `/preview/`, hidden from search engines. There is one preview at a time,
   and the rest of the website is built again from the main branch. The comment links it and lists anything unusual: a
   number far above the best so far, no coverage, repeats that differ a lot, many cases that hit their time limit or
   branches that differ on a second replay. These flags ask a person to look and stop nothing. A submission that fails
   the checks says why, and the pull request stays red.
7. A maintainer looks at the preview and at Files changed, and merges. See [What the merge does](#what-the-merge-does).

A new push to the pull request cancels the running workflow and starts a new one that needs approval again.

## When something fails

| Message in the comment | What it means | What to do |
|---|---|---|
| The manifest or form details are wrong | The file does not follow the schema or a name or link is not allowed | Nothing for the maintainer. The submitter fixes the file and pushes. |
| Download failed (names the link) | Three tries failed | Check the link. If the host was down, re-run the failed jobs later. |
| Checksum mismatch | The file is not the one the manifest names | The submitter fixes the link or the hash. |
| A submission changes only its manifest | The pull request also changes other files | The submitter takes them out. |
| A published system is already called this | The name of the manifest is taken | The submitter renames the file. |
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

## What the merge does

Merging a submission starts `.github/workflows/publish.yml`. It runs only after a merge, from the main branch, and it runs
nothing from the submission. It does this:

1. It finds a passed replay of exactly the merged commit. There must be one, and its plan must name this pull request.
2. It reads the artifacts as data. `data-coverage-<program>-r<N>` gives the branch set, the curve and the counts of a run.
   `data-signatures-<program>` gives the sanitiser kinds and counts. The details of signatures (frames, examples and case
   ids) never leave the runner of the replay, and two free-text fields of `replay.json` are emptied before upload.
3. It adds the runs to `sets/`, `curves/` and `runs.csv`, the system to `systems.csv` and the tool to `tools.csv` if it is
   new. A program with fewer than three repeats carries the note `incomplete` and a falling curve states its fall. The
   name, version, family, budget and notes come from the manifest. The backend is `other`, the group `main` and the
   configuration `unknown`, so a maintainer who knows better corrects them in a later pull request.
4. It runs `scripts/derive.py` and `scripts/check.py`, and pushes only if they pass.
5. It builds the website, checks the build and publishes it. A job that can do nothing except publish pages does that last
   step. The entry is marked new for 30 days.

If any step fails nothing is pushed or published and a comment on the pull request says why. Fix the cause and re-run the
job from the Actions tab. A change to a manifest that is already published adds nothing.

Merge within 90 days of the replay, because GitHub keeps its results for that long. A merge after that says so on the pull
request. Open a new pull request that changes the manifest, a note is enough, and replay it.

## Going back

Open Actions here, choose "Publish" and press Run workflow:

* **Withdraw** takes the `system_id` of an entry. It stays in the data with the status `withdrawn`, leaves the comparison
  and the website updates.
* **Show an earlier commit** takes a commit of this repository and publishes the website from its data, for a mistake that
  needs undoing at once. The next merge publishes the latest data again, so also fix the data.

Running it with both fields empty rebuilds the website from the latest data, for example after a change to the site's code.

## Snapshots

The data moves on with each merge and git keeps the history. When a paper needs fixed numbers to cite, tag that commit
`v<version>`, set `version`, `release` and `snapshot_date` in `RELEASE.json` and archive it. Links on the website name the
exact commit they were built from.

## Limits

* The free plan runs 20 jobs at a time. A manifest can list at most 256 bundles because a matrix holds no more.
* One job lasts at most 6 hours. The workflow stops a replay after 5 hours 40 minutes and reports it. A sanitiser job
  shares that time between all repeats of its program.
* The disk of a free runner is about 14 GB. The workflow measures the free space at run time and refuses a bundle
  that does not fit.
* DOIs work for Zenodo records that hold one file. Any other link must be a direct https address without
  ampersands, semicolons or spaces.
* Artifacts count against the storage of the account. They are kept for 30 days, so merge within that time.

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
