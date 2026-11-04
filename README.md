# Agent Software Testing results

Release 2026.1, version 0.1.0, 2026-11-02. Copyright 2026 The Agent Software Testing authors.

Agent Software Testing measures test generators the same way. A tool hands in the test cases it produced; one pinned gcov build per program replays them and branch coverage is counted from that replay for every tool. This repository holds the results: for each run of each system on each program, the set of covered branches, a coverage curve, a few derived tables and counts of sanitiser findings. The standard itself (specification, schemas, program definitions, reference runner) is in the `agent-software-testing` repository.

Release 2026.1 holds 28 systems on 18 programs, 848 runs and a branch set and a curve for each run. The first entries are Agolic, an LLM agent that plans symbolic-execution campaigns (reference: arXiv 2608.06397, 2026), its variants and the baselines it was compared with.

## What is here

```
programs.csv    the 18 programs: version, branch total, input channels, source archive, fixed invocation (as in the
                program definitions of the `agent-software-testing` repository)
systems.csv     the 28 systems: family, backend, model, cores, budget, track, measure, group, description, reference,
                tool, version, configuration, submission date, first release, status, the system it supersedes
tools.csv       the 7 tools: name, address, reference, description
runs.csv        one row per run: covered and total branches, measure, paths, output tokens, notes
sets/<system_id>/<program>/r<k>.json.gz     covered branch keys of run k (848 files)
curves/<system_id>/<program>/r<k>.csv       t_s,covered at 900 s checkpoints (848 files)
universe/<program>.txt.gz                   every branch key of the program's pinned build, one per line (18 files)
derived/finals.csv         per system and program: runs, median, minimum, maximum, share of the branch total
derived/overlap.csv        per program and pair of systems: branches only one of them covers, and both
derived/signatures.csv     counts of distinct sanitiser signatures, by system, program, sanitiser and error class
derived/measure_agreement.csv   runs under more than one measure (no rows in this release)
scripts/        derive.py and check.py for the tables above, and the scripts a submission goes through
RELEASE.json    release name, version, date, what the release was measured with, counts, release flag ("public")
LICENSE         Apache-2.0, for the scripts
LICENSE-DATA.md CC BY 4.0, for the data
```

A **run** is one campaign of one system on one program, with a repeat number `k`. Every campaign has a budget of at most six hours (`budget_s` = 21,600 s). The curves are sampled every 900 s from 0 to 21,600 s; each one is non-decreasing and ends at the covered count of its run.

### Branch sets

A branch key is `file:line:block_id:branch_id`, with `file` relative to the source root as lcov and gcov report it. A branch is covered if its execution count is above zero. A set file is a gzip-compressed JSON document

```json
{"schema": "agent-software-testing/branch-set@0.1", "program": "xmlwf", "branch_key_format": "file:line:block_id:branch_id",
 "branch_total": 6293, "branch_covered": 3698, "covered_branch_keys": ["lib/siphash.h:170:0:0", "lib/siphash.h:170:0:1", "..."]}
```

Keys are sorted as strings and unique. `branch_total` is the size of the program's universe on its pinned coverage build (`programs.csv`). Only covered keys are stored; the uncovered ones are the rest of the universe (next section). Some keys of Krep and WOFF2 name system headers by absolute path (for example `/usr/include/ctype.h`), because inline functions from those headers are part of the pinned build's universe; the universes of Krep and WOFF2 hold 16 and 564 such keys. No other key is an absolute path.

### Universes

`universe/<program>.txt.gz` is the universe of a program: every branch of its pinned coverage build, covered or not, as one key per line (gzip-compressed text, keys sorted as strings, 167,357 keys in all). Its size is `branch_total` in `programs.csv` and every covered key of every branch set belongs to it; `scripts/check.py` tests both, so a key of another program or a made-up key is rejected. The branches a run left uncovered are the universe minus its set.

A universe includes bundled code: for jq, 9,795 of the 18,280 branches are in the bundled Oniguruma regular-expression library (`vendor/oniguruma`). Shares of different programs do not compare.

### Measures

Every run was measured the same way. Its test cases were replayed on the pinned gcov build of its program with the reference runner of the `agent-software-testing` repository (profile `standard`, the only value of `measure` in `runs.csv`) and its covered branches were counted from that replay. Because every system is counted this way, the counts of different systems on the same program can be compared.

### How a run gets in

Every run in this release has a branch set and a curve. New runs enter through the replay of the `agent-software-testing` repository: a tool submits its test cases as a bundle and the reference runner replays them. The submission steps are in [docs/SUBMITTING.md](docs/SUBMITTING.md).

### Columns

- `programs.csv`: the columns follow the program definitions of the `agent-software-testing` repository and `scripts/check.py` compares them with those definitions when that repository is checked out next to this one. `version` is the upstream release the pinned source corresponds to (Krep 1.2, Oggenc 1.4.3, WOFF2 1.0.2); the exact source is the archive in `source_url`, pinned by commit where upstream has no release archive. `inputs` lists the input channels a test case may use (`argv`, `files`, `stdin`, `env`). `fixed_track` is the invocation of the fixed track: an argument list in which `{input}` stands for the input file or `stdin` when the input arrives on standard input (the harnesses of Confetti, Libmatheval and LibYAML read standard input); it is empty for the other programs.
- `systems.csv`: `family` is `symbolic`, `fuzzer`, `hybrid`, `llm`, `agent` or `other`. `group` is `main`, `ablation`, `seeding` or `control`. `cores` and `budget_s` are the resources of one campaign; `budget_s` is 21,600 for every system and `cores` is empty for ConcoLLMic and for the AFL++ control. `track` is `open` when the system chooses command-line arguments, files, standard input and environment and `fixed` when the program's fixed invocation is given and only the input is supplied. The KLEE baselines and the SymTuner systems are listed as `open`; their descriptions say that they run under KLEE's symbolic-argument harness, in which the harness decides which arguments, files and standard input are symbolic and how large. `measure` is `standard` for every system in this release.
- `systems.csv`, identity columns. An entry is a tool in one version and configuration with a model, a track and a budget. Two entries that agree in all of these are one entry and any difference makes a new one. `system_id` is the key that the paths in `sets/` and `curves/` use and `tool_id` names the tool in `tools.csv`. `version` is free text. Where a label alone does not name one code archive, a plus sign and the first eight hexadecimal digits of the SHA-256 of that archive follow it (Agolic `1.0.1+2407fd07`, because the label `1.0.1` alone covers more than one code tree). `config` is a short label of the configuration and `config_sha256` the SHA-256 of the configuration file or command line or `unknown` (it is `unknown` for every system in this release). `submitted` is the date the entry reached this repository and `first_release` the release that first held it (2026.1 for all 28 systems). `status` is `current`, `superseded` or `withdrawn` and `supersedes` names the entry that this one replaces. Latest means newest submitted and never best. The word `unknown` stands where no evidence establishes a value.
- `tools.csv`: one row per tool. `url` and `reference` are empty where none is recorded.
- `RELEASE.json`: `release` names the release (`YYYY.N`; this one is 2026.1) and `previous_release` the one before it (null for the first). `version` (0.1.0) is the version number of the repository and `date` the date of the release. `suite`, `specification`, `programs_tag`, `image` and `runner` say what the release was measured with and `profile` is `standard`. A field that is null is not recorded. `withdrawn_since_previous` lists the systems of the previous release that this one does not hold. A release does not change after it is published; a correction, a replacement or a withdrawal goes into the next one.
- `runs.csv`: `covered` and `total` are branches; `output_tokens` is the model output of the whole campaign for the LLM systems, reasoning tokens included, as the provider reported it (empty for systems without a model and where no count was recovered); `notes` is empty for every run in this release.
- `derived/finals.csv`: `median`, `min` and `max` are over all runs in `runs.csv`, whether or not a branch set is held; `median_pct` is the median as a percentage of `total`.
- `derived/overlap.csv`: unions over the runs of each system, compared on the branches of the program's universe (every run in this release is counted on the full universe). A program enters for a pair of systems only when both have repeats 1, 2 and 3 on it; the one-worker systems also have repeats 4 and 5, which are not used. Pairs are listed once, with `system_a` before `system_b` in the order of `systems.csv`.
- `derived/signatures.csv`: see Sanitiser counts.

## Systems

<!-- systems:start -->
| System | Group | Family | Programs | Runs | Branch sets | Measure |
|---|---|---|---:|---:|---:|---|
| `agolic-klee-gpt55` | main | agent | 18 | 54 | 54 | standard |
| `agolic-klee-deepseek` | main | agent | 9 | 27 | 27 | standard |
| `agolic-direct-deepseek` | main | agent | 9 | 27 | 27 | standard |
| `agolic-aflpp-deepseek` | main | agent | 4 | 12 | 12 | standard |
| `concollmic-gpt55` | main | llm | 7 | 17 | 17 | standard |
| `concollmic-deepseek` | main | llm | 3 | 9 | 9 | standard |
| `klee-fixed-6w` | main | symbolic | 18 | 54 | 54 | standard |
| `klee-uniform-6w` | main | symbolic | 18 | 54 | 54 | standard |
| `symtuner-6w` | main | symbolic | 18 | 54 | 54 | standard |
| `aflpp` | main | fuzzer | 6 | 18 | 18 | standard |
| `symcc-aflpp` | main | hybrid | 6 | 18 | 18 | standard |
| `symsan-aflpp` | main | hybrid | 6 | 18 | 18 | standard |
| `aflpp-rebuilt-control` | control | fuzzer | 4 | 12 | 12 | standard |
| `klee-fixed-1w` | control | symbolic | 18 | 90 | 90 | standard |
| `klee-uniform-1w` | control | symbolic | 18 | 90 | 90 | standard |
| `symtuner-1w` | control | symbolic | 18 | 90 | 90 | standard |
| `agolic-klee-gpt54` | ablation | agent | 18 | 54 | 54 | standard |
| `agolic-klee-gpt55-effort-low` | ablation | agent | 4 | 12 | 12 | standard |
| `agolic-klee-gpt55-effort-xhigh` | ablation | agent | 4 | 12 | 12 | standard |
| `agolic-klee-gpt55-harness-entry-only` | ablation | agent | 4 | 12 | 12 | standard |
| `agolic-klee-gpt55-seeded-prefix-only` | ablation | agent | 4 | 12 | 12 | standard |
| `agolic-klee-gpt55-seed-finder-off` | ablation | agent | 4 | 12 | 12 | standard |
| `agolic-klee-gpt55-proposal-review-off` | ablation | agent | 4 | 12 | 12 | standard |
| `agolic-klee-gpt55-history-withheld` | ablation | agent | 4 | 12 | 12 | standard |
| `agolic-klee-deepseek-effort-low` | ablation | agent | 4 | 12 | 12 | standard |
| `agolic-klee-deepseek-effort-high` | ablation | agent | 4 | 12 | 12 | standard |
| `symtuner-6w-rerun` | seeding | symbolic | 7 | 21 | 21 | standard |
| `symtuner-6w-rerun-seeded-tests-only` | seeding | symbolic | 7 | 21 | 21 | standard |
<!-- systems:end -->

Programs: bc, Confetti, diff, du, gawk, gcal, grep, gzip, jq, Krep, Libmatheval, LibYAML, ls, Oggenc, sed, WOFF2, xmlwf and xorriso (`programs.csv`). Six of them (Confetti, Krep, Libmatheval, LibYAML, Oggenc, WOFF2) have a harness that takes one input, from a file or from standard input and so a fixed track; AFL++, SymCC and SymSan ran there.

Notes on individual systems.

- Agolic variants run with six cores for six hours; AFL++, SymCC and SymSan use one core; the KLEE baselines use six workers or one.
- `agolic-klee-gpt54` is the agent with GPT-5.4 at medium reasoning effort (source commit e19bd1b), on all 18 programs with three repeats. It is marked superseded by `agolic-klee-gpt55`. Its campaigns kept no record of when each test case was made, so the times of its curves are reconstructed from the modification times of the test files.
- The ablation systems of Agolic with GPT-5.5 are named by condition: `harness-entry-only`, `seeded-prefix-only`, `seed-finder-off`, `proposal-review-off`, `history-withheld`, `effort-low` and `effort-xhigh`. Each runs on Confetti, gzip, Krep and WOFF2, three repeats.
- `agolic-klee-deepseek` runs on nine programs: Confetti, gzip, Krep, WOFF2, diff, du, gawk, ls and xmlwf.
- `symtuner-6w-rerun-seeded-tests-only` starts SymTuner from the seeds that an Agolic campaign with GPT-5.5 produced for the same program and counts only the tests SymTuner generated.
- ConcoLLMic stops after 30 minutes without new coverage or after six hours. With GPT-5.5, three of its seven programs have fewer than three runs: Confetti (2), Libmatheval (2) and WOFF2 (1).
- SymSan is a reconstruction from its source, not the authors' binary.

## Replay with the reference runner

The tests of 848 runs of 28 systems were replayed with the reference runner of the `agent-software-testing` repository (profile `standard`) on the pinned build of each program, in a container with a process view of its own (SPEC section 6.7).

## Caveats

- **Unknown values.** The version of Agolic with the run history withheld is `unknown`. `config_sha256` is `unknown` for every system.
- **Output tokens.** They are not held for Agolic with GPT-5.4, nor for five of the 17 ConcoLLMic runs with GPT-5.5 (Confetti repeats 2 and 3, Libmatheval repeats 1 and 3, WOFF2 repeat 1).
- **Programs.** Not every system was run on every program. The Systems table gives the number of programs of each system.

## Sanitiser counts

Every run was also replayed on an ASan build and on a UBSan build. A signature is the triple (sanitiser, error kind, first stack frame in the program's own source). `derived/signatures.csv` publishes only the number of distinct signatures, grouped by system, program, sanitiser and error class, together with `runs`, the number of runs of that system on that program that were replayed (in this release, all of them). The signatures are counted over the union of those runs. The error class is the sanitiser's own class with the types, sizes and values of the report text removed, for example `heap-buffer-overflow` or `left shift out of range`; `none` means that no signature was found (`signatures` is 0). Frames, stack traces, file names, function names, inputs, case hashes and descriptions of the causes are not published. The counts include benign findings and sanitiser artefacts and are not a count of bugs; two signatures can share a cause.

Kinds and counts are published for every program. Locations, stack traces and inputs of bugs not yet fixed upstream are not published and the inputs for those programs stay out of any public bundle (`DISCLOSURE.md`).

## Not included

- Test cases, inputs, stack traces and bug reports. The sets contain only branch identities.

## Rebuilding the tables

```
python scripts/derive.py               # derived tables and the systems table above
python scripts/check.py                # all checks; exit status 0 when they pass
```

Both need only this repository and the Python standard library. `derived/signatures.csv` is written by `scripts/add_signatures.py`. The identity of the systems (tools, versions, status) is in `systems.csv` and `tools.csv`, and a change is made there. `check.py` compares `programs.csv` with the program definitions when the `agent-software-testing` repository is checked out next to this one and says so when it is not.

## Licence and citation

The data (every file except `LICENSE`, `scripts/` and `tests/`) is licensed under CC BY 4.0, see `LICENSE-DATA.md`. The scripts and tests are licensed under Apache-2.0, see `LICENSE`; each script carries an SPDX identifier and the copyright line "Copyright 2026 The Agent Software Testing authors". Branch keys identify lines of third-party programs by file name, line and block; those programs keep their own licences.

Please cite the preprint by Koh, Noller, Pasareanu and Sun, "Agentic Planning for Symbolic Execution", arXiv 2608.06397 (2026).

## Contributing

New results enter as test cases, never as coverage numbers: see [docs/SUBMITTING.md](docs/SUBMITTING.md) for the
submission steps and [submissions/README.md](submissions/README.md) for the manifest. Report a wrong or mislabelled
number with the data-issue form. Bugs found in the 18 programs follow [DISCLOSURE.md](DISCLOSURE.md).
Contributions follow the [Code of Conduct](CODE_OF_CONDUCT.md); more in [CONTRIBUTING.md](CONTRIBUTING.md).
