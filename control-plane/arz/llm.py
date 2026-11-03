"""Vultr Serverless Inference client (OpenAI-compatible chat completions, stdlib only).

Every call returns provenance for the receipt: model, latency, token usage and the SHA-256 of
the exact request and response bodies. Prompts and responses themselves stay on the control
plane (in the job directory); the receipt pins their hashes.

Environment: VULTR_INFERENCE_API_KEY, VULTR_INFERENCE_URL, ARZ_MODEL (default), ARZ_MODEL_ESCALATION.
"""

import hashlib
import json
import os
import time
import urllib.error
import urllib.request


class LLMError(RuntimeError):
    pass


def default_model():
    return os.environ.get("ARZ_MODEL", "qwen3.8-flash-next")


def escalation_model():
    return os.environ.get("ARZ_MODEL_ESCALATION", "glm-5.3")


def _post(body, timeout):
    url = os.environ.get("VULTR_INFERENCE_URL", "https://api.vultrinference.com/v1").rstrip("/") + "/chat/completions"
    data = json.dumps(body, sort_keys=True).encode()
    for attempt in range(4):
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Authorization": f"Bearer {os.environ['VULTR_INFERENCE_API_KEY']}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return data, r.read()
        except urllib.error.HTTPError as e:
            if (e.code == 429 or e.code >= 500) and attempt < 3:
                time.sleep(3 * (attempt + 1))
                continue
            raise LLMError(f"inference HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < 3:
                time.sleep(3 * (attempt + 1))
                continue
            raise LLMError(f"inference unreachable: {e}") from None
    raise LLMError("inference: retries exhausted")


def chat_json(messages, schema, name, model=None, reasoning_effort="low", max_tokens=8000, timeout=240):
    """One chat call constrained to a strict JSON schema. Returns (parsed_object, provenance).

    reasoning_effort must be set: with the default effort several models spend the whole token
    budget reasoning and return empty content (spikes/inference/FINDINGS.md).
    """
    model = model or default_model()
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "reasoning_effort": reasoning_effort,
            "response_format": {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}}
    t0 = time.monotonic()
    request_bytes, raw = _post(body, timeout)
    latency = time.monotonic() - t0
    resp = json.loads(raw)
    choice = (resp.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content") or ""
    usage = resp.get("usage") or {}
    prov = {"model": resp.get("model", model), "purpose": name, "latency_s": round(latency, 2),
            "finish_reason": choice.get("finish_reason"),
            "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
            "request_sha256": hashlib.sha256(request_bytes).hexdigest(),
            "response_sha256": hashlib.sha256(raw).hexdigest()}
    try:
        obj = json.loads(content)
    except ValueError:
        raise LLMError(f"{model} returned non-JSON content for {name} "
                       f"(finish_reason={choice.get('finish_reason')}): {content[:200]!r}") from None
    return obj, prov, request_bytes, raw
