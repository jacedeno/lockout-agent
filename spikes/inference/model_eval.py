"""Quick kickoff eval of Vultr Serverless Inference chat models.

Test 1: JSON-schema reply carrying Python code; the code runs in a locked Podman container
        against a synthetic signal with a known answer.
Test 2: native tool call with valid JSON arguments.
"""
import concurrent.futures as cf
import json
import os
import subprocess
import time
import urllib.request

URL = os.environ["VULTR_INFERENCE_URL"].rstrip("/") + "/chat/completions"
KEY = os.environ["VULTR_INFERENCE_API_KEY"]
MODELS = [
    "qwen3.8-flash-next", "qwen3.8-27b", "glm-5.3", "glm-5.3-flash",
    "deepseek-v4.1-flash-1017", "mimo-v2.6-pro", "mimo-v2.6-flash", "minimax-m3.1-flash",
]

SCHEMA = {
    "type": "object",
    "properties": {"code": {"type": "string"}, "explanation": {"type": "string"}},
    "required": ["code", "explanation"],
    "additionalProperties": False,
}
CODE_PROMPT = (
    "Write self-contained Python 3 code (numpy only) that defines "
    "velocity_rms_mm_s(accel_g, fs) -> float. It takes a 1-D acceleration signal in g "
    "(1 g = 9.80665 m/s^2) sampled at fs Hz and returns the RMS vibration velocity in mm/s "
    "restricted to the 10-1000 Hz band (ISO 20816), integrating in the frequency domain. "
    "Define the function only: no example usage, no file or network access, no printing. "
    "Reply as JSON with keys code and explanation."
)
HARNESS = """
import numpy as np
fs = 12000.0
t = np.arange(int(fs)) / fs
sig = 1.0*np.sin(2*np.pi*100*t) + 0.5*np.sin(2*np.pi*5*t)
print(repr(float(velocity_rms_mm_s(sig, fs))))
"""
EXPECTED = 9.80665 / (2 * 3.141592653589793 * 100) * 1000 / 2 ** 0.5  # 11.04 mm/s

TOOLS = [{
    "type": "function",
    "function": {
        "name": "run_python",
        "description": "Run Python 3 code in an isolated sandbox with numpy and scipy. "
                       "The dataset is mounted read-only at /data. Returns stdout and stderr.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python source to run"},
                "timeout_s": {"type": "integer", "description": "Wall-clock limit, 1-120"},
            },
            "required": ["code"],
        },
    },
}]
TOOL_PROMPT = ("/data/pump.csv has columns t,accel_g sampled at 25.6 kHz. "
               "Find the dominant vibration frequency. Use the tool.")


EXTRA = json.loads(os.environ.get("EVAL_EXTRA", "{}"))


def chat(body):
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {KEY}",
                                          "Content-Type": "application/json"})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=240) as r:
        data = json.load(r)
    return data, time.monotonic() - t0


def run_locked(code):
    p = subprocess.run(
        ["podman", "run", "--rm", "-i", "--network", "none", "--read-only", "--tmpfs", "/tmp",
         "--memory", "512m", "--pids-limit", "128", "--cpus", "1", "--cap-drop", "ALL",
         "--security-opt", "no-new-privileges", "--user", "65534",
         "--entrypoint", "python3", "localhost/arz-runner:latest", "-"],
        input=code + "\n" + HARNESS, capture_output=True, text=True, timeout=60)
    return p.stdout.strip(), p.stderr.strip()[-300:]


def eval_model(m):
    out = {"model": m}
    try:
        d, dt = chat({"model": m, "messages": [{"role": "user", "content": CODE_PROMPT}],
                      "response_format": {"type": "json_schema",
                                          "json_schema": {"name": "analysis", "strict": True,
                                                          "schema": SCHEMA}},
                      "max_tokens": 8000, **EXTRA})
        out["code_s"] = round(dt, 1)
        out["code_tokens"] = d.get("usage", {}).get("completion_tokens")
        content = d["choices"][0]["message"].get("content") or ""
        try:
            obj = json.loads(content)
            out["json_ok"] = set(obj) == {"code", "explanation"}
            stdout, stderr = run_locked(obj["code"])
            try:
                val = float(stdout.splitlines()[-1])
                out["value"] = round(val, 2)
                out["correct"] = abs(val - EXPECTED) / EXPECTED < 0.05
            except Exception:
                out["correct"] = False
                out["run_err"] = stderr or stdout[-200:]
        except json.JSONDecodeError:
            out["json_ok"] = False
            out["raw"] = content[:150]
    except Exception as e:
        out["code_err"] = repr(e)[:200]
    try:
        d, dt = chat({"model": m, "messages": [{"role": "user", "content": TOOL_PROMPT}],
                      "tools": TOOLS, "tool_choice": "auto", "max_tokens": 8000, **EXTRA})
        out["tool_s"] = round(dt, 1)
        msg = d["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        ok = False
        if calls and calls[0]["function"]["name"] == "run_python":
            args = json.loads(calls[0]["function"]["arguments"])
            ok = "code" in args and len(args["code"]) > 20
            out["tool_calls"] = len(calls)
        out["tool_ok"] = ok
    except Exception as e:
        out["tool_err"] = repr(e)[:200]
    return out


if __name__ == "__main__":
    print(f"expected {EXPECTED:.2f} mm/s")
    with cf.ThreadPoolExecutor(len(MODELS)) as ex:
        for r in ex.map(eval_model, MODELS):
            print(json.dumps(r))
