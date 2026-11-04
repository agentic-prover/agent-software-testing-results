# Disclosure policy: bugs found in the programs

Agent Software Testing replays test cases from many tools through real programs. Some of those cases make a program crash or make a
sanitiser report a memory error or undefined behaviour. Such a finding may be a genuine bug in a program that people
use. This policy says how we handle findings so that Agent Software Testing helps the programs it replays and never exposes their
users.

The short version: **a bug found through a replay is never made public before it is fixed upstream or before 90 days
have passed since the maintainers reported it, whichever comes first. The maintainers report it to the program's
maintainers. Submitters, readers and contributors must not publish details of an unfixed bug that a replay found.**

## What this covers

* Any crash, hang, sanitiser report (AddressSanitizer, UndefinedBehaviorSanitizer) or other misbehaviour of one of the
  programs that appears when test cases are replayed by Agent Software Testing: by the maintainers, in a submission or by you with
  the pinned builds.
* The information about it beyond the published kinds and counts: the code location, the function and file names, the
  stack trace, the error report, the input that triggers it and a description of the cause.

It does not cover the published counts (below), nor bugs that the upstream project has already fixed or made public.

## What we publish and what we do not

* **Published:** counts of distinct sanitiser signatures per system, program, sanitiser and error class
  (`derived/signatures.csv`) and coverage. A count is not a count of bugs: signatures include benign findings and
  sanitiser artefacts and two signatures can share a cause (`SPEC.md` section 8).
* **Not published:** signatures with their frames, function names, file names, stack traces, sanitiser output, the
  inputs behind a finding, case hashes and descriptions of causes. These stay in the private files of the replay
  (`signatures-private.json`), which are never part of a release.
* **Kinds and counts are published for every program,** including programs with a reported bug that is not yet
  fixed. A kind is the sanitiser's own class, such as `heap-use-after-free` or `SEGV`; it names no location. Locations,
  stack traces and inputs of bugs not yet fixed upstream are not published and the inputs for those programs stay
  out of any public bundle.
* After the fix is public or after 90 days, the maintainers may describe the bug, credit the people who found and
  reported it.

## What the maintainers do

1. **Collect.** The sanitiser pass of a replay writes the private files. Only the maintainers read them.
2. **Triage.** A maintainer decides whether a signature points to a genuine bug in the program, rather than an artefact
   of the sanitiser, the build or the replay. They check whether the bug is already known or fixed in a newer upstream
   version.
3. **Report upstream.** The maintainers report each genuine, unfixed bug with the smallest input that triggers it.
   Reports go to each project's maintainers through the project's own reporting channel. The date of the report starts
   the 90 days.
4. **Follow up.** They answer the upstream maintainers' questions and tell them if a new finding changes the picture.
5. **Publish.** When the bug is fixed upstream and the fix is public or when 90 days have passed since the report,
   whichever comes first, the withheld details may be published as described above.

## What submitters, contributors and readers must do

* Do **not** publish details of an unfixed bug that a replay found: not in an issue, a pull request, a discussion, a
  paper, a blog post or a social-media post. Counts by error class (the counts the repository publishes) are fine.
* **Bundles contain inputs.** A bundle holds every test case a tool produced, including the ones that crash a program.
  Run `python -m agent_software_testing signatures` on your bundles before you upload them (`docs/SUBMITTING.md`). If it reports
  signatures, upload the archive of that program as a restricted or embargoed record or leave out the cases that crash
  the program, until the bug is fixed or the 90 days have passed. A bundle that is already public cannot be
  unpublished, so if you find out afterwards, tell the maintainers at once; they report the bug upstream without delay.
  The 90 days are counted from the date of that report, not from the date of your upload.
* Do not exploit a bug and do not test it against systems you do not own.
* **If you find a bug yourself,** outside a replay or before you submit, you are free to report it to the program's
  maintainers. Tell us if it also appears in our counts so that we do not duplicate the report. If you want us to report it
  for you, send it privately as described below.
* If you are unsure whether something is covered, treat it as covered and ask the maintainers privately.

## How to report a finding to the maintainers

Use GitHub's private vulnerability reporting of this repository: open the *Security* tab and choose *Report a
vulnerability*. Describe the program and version, the input and how to reproduce it (the smallest case you have) and
whether you or anyone else has told the upstream project. Do not open a public issue. The maintainers will triage it
and, if it is genuine and unfixed, report it upstream as above. A finding in the runner or in the sandbox, as opposed
to one of the programs, is reported through the security policy of the `agent-software-testing` repository.

## Limits of this policy

We cannot control what upstream projects do. If upstream does not answer, the 90 days still run. They are the earliest
date at which details may become public, not a promise to publish on that date; before we publish, we try once more to
reach the project and tell them the date.
