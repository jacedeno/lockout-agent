"""Spectral building blocks: velocity RMS, power spectra, tone amplitudes, envelope spectrum.

Conventions: acceleration in g, velocity in mm/s RMS, frequencies in Hz. Every function takes a
1-D signal and its sampling rate and removes the mean first.
"""

import math

import numpy as np
from scipy.signal import butter, hilbert, sosfiltfilt

G = 9.80665  # m/s² per g
MM_S2_PER_G = G * 1000.0


def _detrend(x):
    x = np.asarray(x, dtype=float)
    if x.ndim != 1 or len(x) < 16:
        raise ValueError("expected a 1-D signal with at least 16 samples")
    return x - x.mean()


def velocity_rms(accel_g, fs, band=(10.0, 1000.0)):
    """RMS vibration velocity (mm/s) in `band`, by integration in the frequency domain.

    ISO 20816 assesses broadband velocity RMS over 10–1000 Hz. No window is applied, so
    Parseval holds and the result is the true band-limited RMS.
    """
    a = _detrend(accel_g) * MM_S2_PER_G
    n = len(a)
    spec = np.fft.rfft(a)
    f = np.fft.rfftfreq(n, 1.0 / fs)
    m = (f >= band[0]) & (f <= min(band[1], fs / 2 * 0.999)) & (f > 0)
    v = spec[m] / (2j * math.pi * f[m])
    return float(math.sqrt(2.0 * float(np.sum(np.abs(v) ** 2))) / n)


def power_spectrum(x, fs):
    """Single-sided linear power spectrum with a Hann window.

    Returns (f, ps, enbw_bins). A pure tone of RMS r shows as r² at its peak bin; the tone's
    full power is the sum over its main lobe divided by enbw_bins (1.5 for Hann).
    """
    x = _detrend(x)
    n = len(x)
    w = np.hanning(n)
    s1, s2 = w.sum(), float(np.sum(w * w))
    spec = np.fft.rfft(x * w)
    ps = 2.0 * np.abs(spec) ** 2 / s1 ** 2
    ps[0] /= 2.0
    if n % 2 == 0:
        ps[-1] /= 2.0
    return np.fft.rfftfreq(n, 1.0 / fs), ps, n * s2 / s1 ** 2


def velocity_power_spectrum(accel_g, fs):
    """Power spectrum of velocity ((mm/s)² per bin) from an acceleration signal in g."""
    f, ps, enbw = power_spectrum(accel_g, fs)
    gain = np.zeros_like(f)
    gain[1:] = (MM_S2_PER_G / (2 * math.pi * f[1:])) ** 2
    return f, ps * gain, enbw


def tone_rms(f, ps, enbw, f0, tol_hz, lobe_bins=3):
    """RMS of the strongest tone within f0 ± tol_hz. Returns (rms, frequency_found)."""
    df = f[1] - f[0]
    tol = max(tol_hz, 2 * df)
    idx = np.flatnonzero(np.abs(f - f0) <= tol)
    if len(idx) == 0:
        return 0.0, float(f0)
    k = int(idx[np.argmax(ps[idx])])
    lo, hi = max(k - lobe_bins, 1), min(k + lobe_bins + 1, len(ps))
    return float(math.sqrt(float(ps[lo:hi].sum()) / enbw)), float(f[k])


def order_amplitudes(accel_g, fs, rpm, orders=(1, 2, 3), tol=0.015):
    """Velocity RMS (mm/s) at each running-speed order, searching ±tol·f around it."""
    f, ps, enbw = velocity_power_spectrum(accel_g, fs)
    fr = rpm / 60.0
    out = {}
    for o in orders:
        rms, found = tone_rms(f, ps, enbw, o * fr, tol * o * fr)
        out[f"{o}x"] = {"mm_s": round(rms, 3), "freq_hz": round(found, 2)}
    return out


def default_envelope_band(fs):
    hi = min(6000.0, 0.45 * fs)
    if hi <= 2500.0:
        raise ValueError(f"fs={fs} Hz is too low for envelope analysis (needs > 5.6 kHz)")
    return (2000.0, hi)


def envelope_spectrum(accel_g, fs, band=None):
    """Amplitude spectrum (g RMS per bin) of the demodulated envelope in `band`.

    Bearing defects strike a structural resonance at the defect rate; band-passing around the
    resonance and taking the Hilbert envelope turns those strikes into lines at the defect
    frequency and its harmonics, even when the overall velocity level looks healthy.
    Returns (f, amplitude, enbw_bins, band).
    """
    band = band or default_envelope_band(fs)
    sos = butter(4, band, btype="bandpass", fs=fs, output="sos")
    y = sosfiltfilt(sos, _detrend(accel_g))
    env = np.abs(hilbert(y))
    f, ps, enbw = power_spectrum(env, fs)
    return f, np.sqrt(ps), enbw, band
