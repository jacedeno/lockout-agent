# data

## Synthetic (`synth/`)

`uv run data/synth/generate.py` writes five triaxial datasets (12.8 kHz, 5 s, g) with full
ground truth into `data/synth/out/` (git-ignored). Deterministic: compare with
`data/synth/SHA256SUMS.expected`.

| Scenario | Fault | ISO 20816-3 zone (group 2, rigid) | Velocity RMS x/y/z (mm/s) |
|---|---|---|---|
| `healthy` | none | A | 0.64 / 0.54 / 0.24 |
| `unbalance` | unbalance (1×) | D | 5.23 / 4.42 / 0.61 |
| `misalignment` | misalignment (2× radial, high axial) | C | 3.27 / 2.65 / 2.67 |
| `bearing_outer_race` | BPFO impacts on a 3.2 kHz resonance | **A** — overall level looks fine; only the envelope spectrum shows it | 0.93 / 0.82 / 0.32 |
| `prompt_injection` | unbalance, with a prompt injection in the `notes` field | D | 5.23 / 4.43 / 0.61 |

Machine: 1780 rpm, 75 kW motor + pump, 6205-2RS drive-end bearing.

## Public (`public/`)

`uv run data/public/fetch_cwru.py` downloads four Case Western Reserve University bearing
files (normal, inner race, ball, outer race; same 6205-2RS bearing), checks their pinned
SHA-256 and converts them to CSV + JSON under `data/raw/cwru/` (git-ignored). CWRU states no
license, so the raw files are never committed. The normal baseline's sampling rate still has
to be confirmed from CWRU's documentation.
