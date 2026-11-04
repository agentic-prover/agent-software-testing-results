# Submitting results for your tool

Agent Software Testing measures **test cases**, not tools. You run your tool wherever and however you like, hand in the test cases
it produced and the maintainers replay them on the pinned build of each program. Your tool is then measured beside the
others on the same programs, in the same way and you do not have to build or run any baseline.

This page takes you from a finished campaign to a published result. The formats are defined in `SPEC.md` of the
[`agent-software-testing`](https://github.com/agentic-prover/agent-software-testing) repository (the reference runner);
the commands below are those of its runner. A submission is a pull request that adds one file to this repository.

## Before you start

You need the runner checkout and Python 3.10 or later (the runner uses only the standard library). Install it once or
put its `src/` directory on the path; every command below then has the same form, `python3 -m agent_software_testing`:

```
git clone https://github.com/agentic-prover/agent-software-testing
RUNNER=$PWD/agent-software-testing          # your checkout of the runner (also used in step 4)
python3 -m pip install -e "$RUNNER"
python3 -m agent_software_testing --help

# or, without installing:
export PYTHONPATH=$RUNNER/src
python3 -m agent_software_testing --help
```

The runner's [README](https://github.com/agentic-prover/agent-software-testing#readme) describes the remaining
options. Replaying (step 4) also needs Docker and the replay image, which you build from the same checkout with
`environment/Dockerfile`. See [Getting the replay image](#getting-the-replay-image) below.

## What we need and what we never accept

* Test cases, with the time at which the tool found each one, in the bundle format (`SPEC.md` section 5).
* At least **three independent repeats** per program and track: campaigns of the same configuration, started afresh with
  their own random seeds (and, for model-driven tools, their own sampling). Replaying one campaign three times is not
  three repeats (SPEC section 9.1). A tool with fewer repeats on a program is kept but marked `incomplete`.
* One **budget** that you state and keep: wall-clock time and cores. Release 0.1 uses six hours; other budgets are
  accepted and labelled. Cases found after the budget are not replayed.

We **never accept coverage numbers**, only test cases. Resources (cores, tokens, CPU time, hardware) are self-reported and
published as such. You decide which of the 18 programs (`programs.csv`) to submit; a result on a program needs its
three repeats. Results of the **fixed** track (the program's fixed invocation is given, your tool supplies the input) and
of the **open** track (your tool chooses command-line arguments, files, standard input and environment) are never
combined.

## Step by step

### 1. Run the tool

Run your campaigns and keep every test case that the tool produced, with the time it was found, measured in
milliseconds from the start of the campaign. Seeds that the campaign started with are at time 0. The pinned program
definitions are in the `programs/` directory of the runner repository; for the fixed track the invocation is the
program's `fixed_track` entry.

### 2. Convert each run to a bundle

A bundle is exactly three things: `tool.json`, `cases.jsonl` and `blobs/`, for **one tool, one program, one track and
one repeat**. For KLEE test files and AFL++ output directories there are adapters:

```
python3 -m agent_software_testing adapt ktest klee-out/ \
    --out bundle/ --program-id gzip \
    --tool-name KLEE --tool-version 3.1 --family symbolic --cores 6 --wall-s 21600

python3 -m agent_software_testing adapt afl \
    --queue out/main/queue --queue out/s1/queue \
    --crashes out/main/crashes \
    --arg=-c --arg=@@ \
    --out bundle/ --program-id gzip --track open \
    --tool-name AFL++ --tool-version 4.30c --family fuzzer --cores 2 --wall-s 21600
```

For any other tool, write the files directly. The full mapping, including a short Python writer, is in
[`docs/adapters.md`](https://github.com/agentic-prover/agent-software-testing/blob/HEAD/docs/adapters.md); the
end-to-end example is [`docs/examples/afl-walkthrough.md`](https://github.com/agentic-prover/agent-software-testing/blob/HEAD/docs/examples/afl-walkthrough.md).
The smallest bundle the validator accepts is [`examples/minimal-bundle/`](../examples/minimal-bundle) in this
repository: two cases on gzip, one passing `-c` on the command line and one reading standard input from a blob.
`argv`, `env` values, paths and `cwd` are base64 (`LWM=` is `-c`, `Lg==` is `.`), which is why the adapters are
easier for anything but the smallest bundle. Set the tool's name, version and family (`symbolic`, `fuzzer`, `hybrid`,
`llm`, `agent` or `other`), the repeat number, the track, the budget (`--wall-s`, which the adapters require and
`--cores`), the model for tools that use one and the resources (`--output-tokens`, `--input-tokens`, `--cpu-s`).
`python3 -m agent_software_testing adapt <kind> --help` lists every option. Do not edit the bundle by hand after the adapter has
written it; run the adapter again.

### 3. Validate

```
python3 -m agent_software_testing validate path/to/bundle
```

The validator checks the schema, the hashes, the limits and the track rules. A bundle that does not validate is not
replayed. Use `--programs DIR` to name the `programs/` directory of your checkout if the validator does not find it.

### 4. Check it yourself (optional, recommended)

Replay the bundle in the reference image, started without network access, with a read-only root file system and with a
process view of its own (`SPEC.md` section 6.7), on the coverage build of the program. Docker gives that process view by
default; under Singularity or Apptainer add `--pid`.

#### Getting the replay image

The image is built from the runner checkout. Its `builds` target compiles the coverage
and ASan+UBSan builds of all 18 programs into `/opt/agent-software-testing/builds` (this takes a while and downloads every
source over HTTPS, verifying each SHA-256):

```
docker build --target builds -t agent-software-testing:builds -f "$RUNNER/environment/Dockerfile" "$RUNNER"
```

[`environment/README.md`](https://github.com/agentic-prover/agent-software-testing/blob/HEAD/environment/README.md)
describes the builds, the per-program builder (`python3 environment/build_programs.py gzip`) and the output layout. A
build must stay at the absolute path it was compiled at, which is why the commands below use the image's
`/opt/agent-software-testing/builds` unchanged.

#### Coverage replay

The image contains the builds but not the runner: mount the checkout and put its `src/` on `PYTHONPATH`. The output directory must not exist or must be empty. It goes inside a folder that is writable by uid 10001 (the image's user), because the runner first writes next to it. The scratch file
system must allow execution, because each worker shard runs a copy of the build from there:

```
BUNDLE=$PWD/path/to/bundle
mkdir -p out && sudo chown 10001 out    # Docker Desktop maps ownership itself; on Linux the image runs as uid 10001

docker run --rm --platform linux/amd64 --network none --read-only \
    --pids-limit 4096 --tmpfs /tmp:exec,size=2g \
    -e PYTHONPATH=/cr/src -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$RUNNER":/cr:ro -v "$BUNDLE":/bundle:ro -v "$PWD/out":/results \
    agent-software-testing:builds python3 -m agent_software_testing replay /bundle \
        --build /opt/agent-software-testing/builds/gzip/gcov --out /results/gzip-r1
```

Change the program id in the `--build` path to the bundle's program. `--platform linux/amd64` matters on
Apple-silicon machines; the image is `linux/amd64`. Add `--workers N` (and a `--work-dir` that already exists and has room for N copies of the build) to use more cores. Run `replay --help` for the other options.

The output directory receives `replay.json`, `timeline.csv`, `coverage-branches.json` and `snapshots/`. The number you
read in `replay.json` is the number the maintainers will get, up to the replay noise that the flake check reports.
Replaying runs the program on your test cases; do not do it outside the container.

#### Sanitiser pass

Before you publish a bundle, also run the sanitiser pass on it (it uses the ASan+UBSan build; the binary defaults to
the program's binary with `obj-gcov/` replaced by `obj-native/`):

```
docker run --rm --platform linux/amd64 --network none --read-only \
    --pids-limit 4096 --tmpfs /tmp:exec,size=2g \
    -e PYTHONPATH=/cr/src -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$RUNNER":/cr:ro -v "$BUNDLE":/bundle:ro -v "$PWD/out":/results \
    agent-software-testing:builds python3 -m agent_software_testing signatures /bundle \
        --build /opt/agent-software-testing/builds/gzip/asan-ubsan --out /results/gzip-r1-signatures
```

`signatures-counts.json` holds counts only. `signatures-private.json` holds frames and example cases; keep it to
yourself. If the pass reports signatures, your bundle contains inputs that crash the program and the program may have a
bug that is not fixed upstream. Read [DISCLOSURE.md](../DISCLOSURE.md) before you upload that bundle.

### 5. Archive the bundles and record their hashes

Put each bundle into one archive file (a `.tar.gz` with the contents of the bundle directory, `tool.json`, `cases.jsonl`
and `blobs/`, at the top level of the archive) and compute its SHA-256:

```
COPYFILE_DISABLE=1 tar -czf gzip-open-r1.tar.gz -C path/to/bundle .    # the setting keeps macOS metadata out of the archive
sha256sum gzip-open-r1.tar.gz        # on macOS: shasum -a 256 gzip-open-r1.tar.gz
```

Upload the archives to a **permanent archive that gives a DOI**; we recommend Zenodo. A stable address of an immutable
file (for example a release asset) is accepted if there is no DOI, because the hash pins the content. Anyone must be
able to download the files without logging in once the results are published. Record, for every archive, the program, the
repeat, the DOI or address and the SHA-256. You keep the rights to your tool and your bundles; choose a licence for the
archive that lets us download and replay it.

If the sanitiser pass in step 4 found signatures, you may want to hold those cases back until the disclosure period of
[DISCLOSURE.md](../DISCLOSURE.md) is over. The replay downloads each archive without logging in, so either leave out the
cases that crash the program or send the submission when that period is over.

### 6. Open a pull request

The pull request is the submission. Fork this repository, add one file `submissions/<system_id>.json` and open a pull
request. Choose the `system_id` yourself from lower-case letters, digits, hyphens and underscores. The file is the
manifest of [`submissions/schema.json`](../submissions/schema.json) (see [`submissions/README.md`](../submissions/README.md)).
It holds

* tool name, version, address and licence; family; track;
* budget (wall-clock seconds, cores) and hardware;
* model and token counts, for tools that use a language model;
* for every bundle, the program, the repeat, the DOI or address and the SHA-256;
* a contact handle and your consent to publish the measurements under CC BY 4.0.

Please give only a handle that you are content to see in a public repository. Do not put email addresses into the
repository. The pull request is the place for any question about the submission.

`python3 scripts/validate_manifest.py submissions/<system_id>.json` checks the file on your machine. When you open the
pull request a check reads the manifest and says at once if something is wrong or missing. Push a fix to the same pull
request and it checks again.

Every submission is approved by hand before anything runs, which can take a few working days. We will say in the pull
request what we are waiting for. After the approval the replay runs by itself. It downloads each archive, compares its
SHA-256 with the manifest, validates the bundle and replays it with the flake check. The sanitiser pass follows when all
replays have passed. One comment on the pull request shows the state of every run. If a run fails, the comment says what
failed and what to do. A test case that crashes a program is normal and does not fail a run.

### 7. What the maintainers do

These are the steps a maintainer takes for an accepted submission. The commands assume a checkout of this repository,
next to the runner checkout (the checks look for a directory named `agent-software-testing` beside this one) and Python 3.10 or
later.

1. **Approve the replay.** Read the manifest and the links in the pull request. Then approve the `replay`
   environment of the workflow run. The workflow is described in [REPLAY-WORKFLOW.md](REPLAY-WORKFLOW.md). It never
   pushes to the repository.

2. **Check the result.** The comment on the pull request gives the final counts, the replay noise and the sanitiser kinds
   and counts. Each run keeps its results as artifacts of the workflow run for 30 days.

3. **Ingest the replay output.** For each run:

   * `coverage-branches.json` → `sets/<system_id>/<program>/r<N>.json.gz` (gzip it; the check reads the compressed
     file);
   * `timeline.csv` → `curves/<system_id>/<program>/r<N>.csv` (the 900 s checkpoints of the standard profile);
   * append one row to `runs.csv` with the columns of its header: `covered` and `total` from `replay.json`, the
     `measure` (`standard` for the reference runner's default profile), the two paths above, the output tokens where a
     model was used and notes. A program with fewer than three repeats carries the note `incomplete`; a run with no
     branch set has no `set_path`, the measure `published` and a note saying `no branch set held`. A curve that falls
     between two checkpoints by more than 10 branches needs a note `curve dips by up to N branches` and a maintainer's
     decision. Only ConcoLLMic runs carry a `standard replay driver <8 hex digits>` note;
   * add the system's sanitiser counts of each program with
     `python3 scripts/add_signatures.py <system_id> <program> --rows signature-rows.csv`, using the file from the
     artifact `data-signatures-<program>`. The workflow counted each signature once over the repeats on its runner;
     the script checks the file and refuses when `derived/signatures.csv` already has rows for that system and program;
   * add one row to `systems.csv` if the system is new: `system_id` (lowercase, from `[a-z0-9.+-]`),
     `label`, `family`, `backend`, `model`, `cores`, `budget_s`, `track`, `group` (`main`, `ablation`, `seeding` or
     `control`; a new tool's own result is `main`), `description`, `reference`, `tool_id`, `version`, `config` (a label or `unknown`), `config_sha256` (empty, `unknown` or 64
     lowercase hex digits), `submitted` (YYYY-MM-DD), `first_release` (the current release name), `status`
     (`current`, `superseded` or `withdrawn`) and `supersedes`. `label`, `backend`, `description` and `reference` must not be empty (a tool with no paper gives its address as `reference`, and a `label` names a budget other than six hours, for example `Tryout, 2 cores, 1 h`); use `other` for a backend that none of the existing values describes. The `measure` column is the
     `+`-joined list of the measures of the system's runs, in the order `canonical`, `standard`, `common-observer`,
     `published`;
   * add a row to `tools.csv` if the tool is new: `tool_id`, `name`, `url`, `reference`, `description`. `url` and `reference` are empty or an https address.

4. **Regenerate and check.** Run `python3 scripts/derive.py` (it rewrites `derived/` and the systems table in
   `README.md`), then update the six counts in `RELEASE.json` (`systems`, `programs`, `universes`, `runs`,
   `runs_with_branch_sets`, `runs_with_curves`).

   Run `python3 scripts/check.py`; it must print `all checks passed`. `check.py` also validates every manifest in
   `submissions/` against `submissions/schema.json` and the release version/date shape.

5. **Merge.** Merge the submission pull request once its replay has passed, then add the runs, the system and tool
   rows and the regenerated files in a follow-up pull request that leaves the manifest as it is, because any change to a
   submission pull request starts its replay again. The submitter can review the result there. Say so if something is
   wrong or unclear. A maintainer merges when `check.py` passes.

6. **Release.** When it is merged, the runs are in the next release (`RELEASE.json`), which gets a version, checksums
   and a DOI. Cite that release when you refer to the numbers.

## Rules

* **Test cases only.** Numbers in your paper do not enter the repository; the replay's numbers do.
* **Resources are self-reported.** Cores, wall time, tokens, CPU time and hardware are published as you state them and
  labelled as such. The runner cannot verify the times of the cases (SPEC section 11).
* **Each measurement is labelled.** Every table shows the track, budget, cores, number of repeats and measure of each
  system. There is no global ranking.
* **Releases do not change.** A published release is never edited. A result can be superseded or withdrawn in a later
  release and corrections go into the errata of the next one.
* **Bugs found through replays are not published by you.** The maintainers report them to the program's maintainers
  (see [DISCLOSURE.md](../DISCLOSURE.md)). You may publish counts, but not details of unfixed bugs.
* **Be honest about what the tool did.** A bundle must contain what your tool produced in the stated budget with the
  stated configuration. We may decline or remove a submission that does not and we say why in the pull request.

## Checklist before you open the pull request

- [ ] At least three independent repeats per program and track, with the same budget and configuration.
- [ ] Every bundle passes `python3 -m agent_software_testing validate`.
- [ ] Every archive is uploaded, downloadable without login and its SHA-256 is recorded.
- [ ] Sanitiser pass run on every bundle; archives that contain crashing inputs are restricted or embargoed.
- [ ] Tool name, version, address, licence, family, track, budget, hardware, model and tokens are filled in.
- [ ] You agree that the measurements are published under CC BY 4.0.
