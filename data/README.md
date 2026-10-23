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
license, so the raw files are never committed.

| File | Condition | fs | Source of fs |
|---|---|---|---|
| 97 | normal, 0 hp, 1796 rpm | **48 kHz** | Inferred: CWRU's pages never state it. A shaft-harmonic comb test fits 48 kHz (38/40 harmonics) and not 12 kHz (23/40). The same test picks 12 kHz for all three fault files, which validates it. |
| 105 | inner race 0.007", 0 hp | 12 kHz | "12k Drive End Bearing Fault Data" set |
| 118 | ball 0.007", 0 hp | 12 kHz | same |
| 130 | outer race 0.007" @ 6:00, 0 hp | 12 kHz | same |

All channels are acceleration in g. `accel_de_g` is the drive-end housing (6205-2RS) and
`accel_fe_g` the fan-end housing (6203-2RS, a different bearing). Resample the normal file
to 12 kHz before comparing it with the fault files. CWRU's "rolling element" defect order
(4.7135 × fr) is twice the ball spin frequency; the synthetic metadata reports the single
BSF (2.357 × fr).
