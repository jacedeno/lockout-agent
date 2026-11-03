"""vibkit against known answers: pure tones, ISO tables, the synthetic set and (if present) CWRU."""

import json
import math
import os
import subprocess
import sys

import numpy as np
import pytest

import vibkit
from vibkit import bearing, iso
from vibkit.spectral import MM_S2_PER_G

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FS = 12_800


def tone_g(freq, v_rms_mm_s, seconds=5.0, fs=FS, phase=0.3):
    t = np.arange(int(fs * seconds)) / fs
    a_peak = 2 * math.pi * freq * math.sqrt(2) * v_rms_mm_s / MM_S2_PER_G
    return a_peak * np.sin(2 * math.pi * freq * t + phase)


# --- building blocks ---------------------------------------------------------------------------

def test_velocity_rms_of_a_tone():
    assert vibkit.velocity_rms(tone_g(29.67, 4.0), FS) == pytest.approx(4.0, rel=0.01)


def test_velocity_rms_ignores_out_of_band_energy():
    x = tone_g(29.67, 4.0) + tone_g(5.0, 50.0) + tone_g(2000.0, 50.0)
    assert vibkit.velocity_rms(x, FS) == pytest.approx(4.0, rel=0.01)


def test_order_amplitudes_off_bin():
    rpm = 1783.3  # 1× = 29.72 Hz, between bins
    x = tone_g(rpm / 60, 3.0) + tone_g(2 * rpm / 60, 1.5)
    o = vibkit.order_amplitudes(x, FS, rpm)
    assert o["1x"]["mm_s"] == pytest.approx(3.0, rel=0.03)
    assert o["2x"]["mm_s"] == pytest.approx(1.5, rel=0.03)
    assert o["3x"]["mm_s"] < 0.05


@pytest.mark.parametrize("v,expected", [(1.39, "A"), (1.4, "B"), (2.79, "B"), (2.8, "C"), (4.49, "C"), (4.5, "D")])
def test_iso_zone_boundaries_group2_rigid(v, expected):
    assert iso.zone(v, iso.boundaries(2, "rigid")) == expected


def test_iso_tables():
    assert iso.boundaries(1, "flexible") == {"A/B": 3.5, "B/C": 7.1, "C/D": 11.0}
    assert iso.group_for_kw(75) == 2 and iso.group_for_kw(500) == 1 and iso.group_for_kw(5) is None
    with pytest.raises(ValueError):
        iso.boundaries(3, "rigid")


def test_defect_frequencies_6205():
    f = bearing.defect_frequencies(1780, 9, 7.94, 39.04)
    assert f["BPFO"] == pytest.approx(106.35, abs=0.01)
    assert f["BPFI"] == pytest.approx(160.65, abs=0.01)
    assert f["BSF"] == pytest.approx(69.92, abs=0.01)
    assert f["FTF"] == pytest.approx(11.82, abs=0.01)


def test_missing_metadata_is_flagged_not_guessed_silently():
    x = {"x": tone_g(29.67, 2.0)}
    d = vibkit.diagnose(x, FS, {"rpm": 1780})
    assert d["iso_20816_3"]["zone"] == "B"
    assert any("group 2" in a for a in d["assumptions"])
    assert any("bearing analysis skipped" in a for a in d["assumptions"])


def test_rpm_required():
    with pytest.raises(ValueError):
        vibkit.diagnose({"x": tone_g(29.67, 2.0)}, FS, {})


# --- the synthetic set (regenerated, deterministic) ---------------------------------------------

@pytest.fixture(scope="session")
def synth(tmp_path_factory):
    out = tmp_path_factory.mktemp("synth")
    subprocess.run([sys.executable, "-I", os.path.join(REPO, "data", "synth", "generate.py"), "--out", str(out)],
                   check=True, capture_output=True)
    return out


SCENARIOS = ["healthy", "unbalance", "misalignment", "bearing_outer_race", "prompt_injection"]


@pytest.mark.parametrize("name", SCENARIOS)
def test_synthetic_ground_truth(synth, name):
    meta = json.load(open(synth / f"{name}.json"))
    gt = meta["ground_truth"]
    d = vibkit.analyze(synth / f"{name}.csv", synth / f"{name}.json")
    assert d["primary_fault"] == gt["fault"]
    assert d["iso_20816_3"]["zone"] == gt["iso_zone"]
    for axis, v in gt["velocity_rms_10_1000hz_mm_s"].items():
        assert d["axes"][axis]["velocity_rms_mm_s"] == pytest.approx(v, abs=0.02)
    for axis, order in gt["dominant_order_by_axis"].items():
        assert d["axes"][axis]["dominant_order"] == f"{order}x"


def test_prompt_injection_changes_nothing(synth):
    a = vibkit.analyze(synth / "unbalance.csv", synth / "unbalance.json")
    b = vibkit.analyze(synth / "prompt_injection.csv", synth / "prompt_injection.json")
    assert (a["primary_fault"], a["iso_20816_3"]["zone"]) == (b["primary_fault"], b["iso_20816_3"]["zone"])
    assert "notes" not in json.dumps(b)


# --- CWRU (public, never committed: fetch with data/public/fetch_cwru.py) -------------------------

CWRU = os.path.join(REPO, "data", "raw", "cwru")
CWRU_CASES = [("cwru_97_normal_0hp", "none"),
              ("cwru_105_inner_race_007_0hp", "bearing_inner_race"),
              ("cwru_130_outer_race_007_6oclock_0hp", "bearing_outer_race"),
              # Known hard case: the 0.007" ball fault shows inner- and outer-race lines of equal
              # strength. vibkit reports a bearing defect without claiming a type.
              ("cwru_118_ball_007_0hp", "bearing_defect")]


@pytest.mark.skipif(not os.path.isdir(CWRU), reason="CWRU data not downloaded")
@pytest.mark.parametrize("name,expected", CWRU_CASES)
def test_cwru(name, expected):
    d = vibkit.analyze(os.path.join(CWRU, f"{name}.csv"), os.path.join(CWRU, f"{name}.json"))
    assert d["primary_fault"] == expected
