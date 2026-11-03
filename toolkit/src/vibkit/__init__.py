"""vibkit: vibration diagnostics for rotating machinery.

    import vibkit
    report = vibkit.analyze("pump.csv", "pump.json")      # full deterministic diagnosis
    signals, fs = vibkit.load_csv("pump.csv")
    vibkit.velocity_rms(signals["x"], fs)                  # mm/s RMS, 10–1000 Hz
    vibkit.order_amplitudes(signals["x"], fs, rpm=1780)    # 1×/2×/3× in mm/s
    vibkit.iso.zone(4.1, vibkit.iso.boundaries(2, "rigid"))
    vibkit.bearing.indicators(signals["x"], fs, {"BPFO": 106.4})

Units: acceleration in g, velocity in mm/s RMS, frequency in Hz.
"""

from . import bearing, iso
from .diagnose import analyze, diagnose
from .io import load_csv, load_metadata
from .spectral import envelope_spectrum, order_amplitudes, power_spectrum, velocity_power_spectrum, velocity_rms

__version__ = "0.1.0"
__all__ = ["analyze", "bearing", "diagnose", "envelope_spectrum", "iso", "load_csv", "load_metadata",
           "order_amplitudes", "power_spectrum", "velocity_power_spectrum", "velocity_rms"]
