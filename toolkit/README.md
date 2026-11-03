# toolkit (vibkit)

Vibration diagnostics library baked into the sandbox's runner image and called by the
LLM-written job script. numpy + scipy only. Units: acceleration in g, velocity in mm/s RMS,
frequency in Hz.

```python
import vibkit
report = vibkit.analyze("pump.csv", "pump.json")        # full deterministic diagnosis (dict)
signals, fs = vibkit.load_csv("pump.csv")                # {"x": array, ...}, Hz
vibkit.velocity_rms(signals["x"], fs)                    # broadband 10–1000 Hz (ISO 20816)
vibkit.order_amplitudes(signals["x"], fs, rpm=1780)      # {"1x": {"mm_s", "freq_hz"}, "2x": …, "3x": …}
vibkit.iso.zone(3.3, vibkit.iso.boundaries(2, "rigid")) # "C"
vibkit.bearing.defect_frequencies(1780, 9, 7.94, 39.04)  # BPFO/BPFI/BSF/FTF in Hz
vibkit.bearing.indicators(signals["x"], fs, {"BPFO": 106.35})   # envelope-spectrum lines + SNR
```

`python -m vibkit data.csv meta.json` prints the diagnosis as JSON.

## What `diagnose` does

| Step | Method |
|---|---|
| Level | Velocity RMS 10–1000 Hz per axis by frequency-domain integration (no window, Parseval exact) |
| Orders | 1×/2×/3× velocity RMS from a Hann power spectrum, main-lobe power over ENBW, ±1.5 % search |
| ISO 20816-3 zone | Worst radial axis against the metadata's boundaries, else the group/foundation table (assumptions are listed, never silent) |
| Unbalance | 1× ≥ 60 % of band energy, 2× < 0.5·1×, axial 1× < 0.5·radial 1×, level above A/B |
| Misalignment | radial 2× ≥ 0.75·1×, or axial 1× ≥ 0.5·radial 1×, level above A/B |
| Bearing defect | 2–6 kHz band-pass, Hilbert envelope, lines at the defect frequency (≥ 10 dB over the local floor) and a harmonic (≥ 6 dB); weaker indications > 15 dB below the strongest are listed separately; two types within 3 dB → `bearing_defect` with no type |

Every finding carries the numbers that triggered it. Free-text metadata (`notes`) is never
read, so a prompt injection there cannot change the result.

## Validation (`uv run pytest -q`, 23 tests)

| Dataset | Result |
|---|---|
| Synthetic: healthy / unbalance / misalignment / bearing outer race / prompt injection | fault, ISO zone, per-axis level (±0.02 mm/s) and dominant order all match the ground truth |
| CWRU 97 normal, 105 inner race, 130 outer race | correct |
| CWRU 118 ball (0.007") | `bearing_defect`, type not claimed: inner- and outer-race lines within 3 dB. A known hard case in this data set |

The CWRU tests are skipped unless `data/public/fetch_cwru.py` has been run (the data is never
committed).
