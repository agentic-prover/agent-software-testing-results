# Submissions

This directory holds the **manifests** of accepted submissions: one JSON file per system, named
`submissions/<system_id>.json`, where `system_id` is the id of the system in `systems.csv`. A manifest records who
submitted what, with which budget and resources and which bundles the results come from, so that every run in
`runs.csv` can be traced to the archive and the SHA-256 of the bundle that was replayed.

The manifest is described by [`schema.json`](schema.json) (schema id `agent-software-testing/submission@0.1`). How to submit is in
[`../docs/SUBMITTING.md`](../docs/SUBMITTING.md). A manifest does not hold coverage numbers: those come from the replay and
are in `runs.csv`, `sets/` and `curves/`.

## Who writes a manifest

The submitter writes the manifest and sends it as a pull request that adds `submissions/<system_id>.json`. The pull
request is the submission. When it is accepted, the maintainers add the `system_id` if it was left out and add the runs
to the same pull request or a follow-up one. The submitter reviews it there. Nothing in a manifest is a promise by the maintainers; the resources, hardware
and tokens are the submitter's own statements and the published tables say so.

## Fields

| Field | Meaning |
|---|---|
| `schema` | `agent-software-testing/submission@0.1` |
| `system_id` | The id in `systems.csv`. It is the name of the file. The maintainers may set it when it is left out. |
| `tool` | `name`, `version` (release, tag or commit that ran), `url`, `licence` (SPDX identifier, `proprietary` or `unknown`), `family` (`symbolic`, `fuzzer`, `hybrid`, `llm`, `agent`, `other`), `model` (language model or `null`), `config_sha256` (optional). |
| `track` | `fixed` or `open`. A manifest has one track; submit the other track as another system. |
| `budget` | `wall_s` (wall-clock seconds of one campaign), `cores`, optional `notes`. |
| `hardware` | `cpu`, `memory_gib`, `gpu` (or `null`), optional `notes`. |
| `tokens` | For tools that use a language model: `input_tokens` and `output_tokens` of one whole campaign, as the provider reported them (reasoning tokens count as output); the median over the repeats. `null` otherwise. |
| `bundles` | One entry per program and repeat: `program`, `repeat`, `doi_or_url`, `sha256` (of the archive file). |
| `contact_handle` | A GitHub handle, published. No email address or personal name is stored here. |
| `consent` | `publish_under_cc_by_4_0` must be `true`. |
| `notes` | Anything a reader of the tables should know. |

Rules that the schema cannot express and that the maintainers check: every (program, repeat) pair occurs once; each
bundle's own `tool.json` agrees with the manifest; each archive matches its hash; a program with fewer than three
repeats is marked `incomplete` in `runs.csv`.

## Example

The values are made up. The hashes are not those of real files.

```json
{
  "schema": "agent-software-testing/submission@0.1",
  "tool": {
    "name": "example-generator",
    "version": "1.0",
    "url": "https://example.org/example-generator",
    "licence": "Apache-2.0",
    "family": "fuzzer",
    "model": null
  },
  "track": "open",
  "budget": { "wall_s": 21600, "cores": 1, "notes": "one process per campaign" },
  "hardware": { "cpu": "64-core x86-64 server", "memory_gib": 256, "gpu": null },
  "tokens": null,
  "bundles": [
    {
      "program": "gzip",
      "repeat": 1,
      "doi_or_url": "10.0000/example-gzip-r1",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
    },
    {
      "program": "gzip",
      "repeat": 2,
      "doi_or_url": "10.0000/example-gzip-r2",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
    },
    {
      "program": "gzip",
      "repeat": 3,
      "doi_or_url": "10.0000/example-gzip-r3",
      "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
    }
  ],
  "contact_handle": "example-handle",
  "consent": { "publish_under_cc_by_4_0": true }
}
```

## Checking a manifest

The repository's own checker is `scripts/validate_manifest.py`; it uses the standard library only:

```
python3 scripts/validate_manifest.py submissions/example.json
```

With no argument it validates every manifest in this directory. `check.py` runs the same check over `submissions/`, so
an accepted manifest is validated with the other release checks. Optional fields (`system_id`, `notes`, `model`,
`config_sha256`, the budget and hardware `notes` and `gpu`) may be left out; `model`, `config_sha256`, `gpu` and the
`notes` of the budget and hardware also accept `null`.
