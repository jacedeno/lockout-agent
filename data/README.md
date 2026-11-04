# data

## Synthetic (`synth/`)

`uv run data/synth/generate.py` writes six triaxial datasets (12.8 kHz, 5 s, g) with full
ground truth into `data/synth/out/` (git-ignored). Deterministic, also across numpy versions:
compare with `data/synth/SHA256SUMS.expected` (the control plane regenerates them on deploy and
refuses to start on a mismatch).

`--trend` (needs vibkit) also writes `trends/<name>.json`: a **synthetic** 12-week history of the
same machine, one reading a week, each generated like the dataset and measured with vibkit; the
last week is the dataset itself. The histories tell each case's story: a steady healthy pump,
unbalance building up after the impeller change, misalignment appearing with the motor
re-install, and bearing defects that grow in the envelope for weeks while the overall level stays
in zone A (the P-F interval). Trends are derived data and not in `SHA256SUMS`.

Besides the fault, every signal carries what a real pump reading does: vane pass (6×) with 1×
sidebands, 2× line frequency, small 3×–5× harmonics, a structural resonance near 1.65 kHz and a
noise floor, so the spectra look like real ones. The ground-truth level is the exact
band-limited RMS of the final signal.

| Scenario | Fault | ISO 20816-3 zone (group 2, rigid) | Velocity RMS x/y/z (mm/s) |
|---|---|---|---|
| `healthy` | none | A | 0.74 / 0.64 / 0.36 |
| `unbalance` | unbalance (1×) | D | 5.25 / 4.44 / 0.67 |
| `misalignment` | misalignment (2× radial, high axial) | C | 3.29 / 2.69 / 2.68 |
| `bearing_inner_race` | BPFI impacts on a 3.6 kHz resonance, modulated by shaft speed (1× sidebands) | **A**, envelope only | 0.97 / 0.91 / 0.43 |
| `bearing_outer_race` | BPFO impacts on a 3.2 kHz resonance | **A** — overall level looks fine; only the envelope spectrum shows it | 1.02 / 0.88 / 0.41 |
| `prompt_injection` | unbalance, with a prompt injection in the `notes` field | D | 5.23 / 4.44 / 0.69 |

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
