"""Loading measurement files: CSV signals and JSON machine metadata."""

import json

import numpy as np

MAX_ROWS = 5_000_000  # ~7 min at 12.8 kHz; refuses absurd inputs instead of exhausting memory
TIME_COLUMNS = ("time_s", "t", "time")


def load_metadata(path):
    with open(path) as f:
        return json.load(f)


def load_csv(path, fs=None):
    """Read a CSV of acceleration channels in g. Returns ({axis: array}, fs).

    Columns named accel_<axis>_g become <axis> (accel_x_g → "x", accel_de_g → "de"); any
    other non-time column keeps its name. fs comes from the argument, else from the time
    column's median step.
    """
    with open(path) as f:
        header = [h.strip() for h in f.readline().split(",")]
        rows = sum(1 for _ in f)
    if rows > MAX_ROWS:
        raise ValueError(f"{path}: {rows} rows is over the {MAX_ROWS} row limit")
    data = np.loadtxt(path, delimiter=",", skiprows=1, ndmin=2)
    if data.shape[1] != len(header):
        raise ValueError(f"{path}: header has {len(header)} columns, data has {data.shape[1]}")
    signals, time = {}, None
    for i, name in enumerate(header):
        if name.lower() in TIME_COLUMNS:
            time = data[:, i]
            continue
        axis = name[6:-2] if name.startswith("accel_") and name.endswith("_g") else name
        signals[axis] = data[:, i]
    if fs is None:
        if time is None or len(time) < 2:
            raise ValueError(f"{path}: no time column; pass fs")
        fs = 1.0 / float(np.median(np.diff(time)))
    return signals, float(fs)
