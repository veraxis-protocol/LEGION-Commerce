"""NVIDIA NIM client for LEGION Commerce demo agents (DEMO-006).

The API key is read from the operator-designated file and never printed, logged or stored.
Every call is recorded (request messages, model, response id, content, usage, latency) by the caller.
"""
import json
import os
import time
import urllib.error
import urllib.request

ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"
MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
KEY_FILE = os.environ.get("NVIDIA_API_KEY_FILE", r"C:\Users\Administrator\Desktop\nvapi.txt")


def chat(messages, schema=None, temperature=0.4, max_tokens=4000, timeout=180, retries=2, thinking=False):
    body = {"model": MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
            # DEMO-006: reasoning traces off (0.5 s vs ~60-170 s per plan; truncation observed with thinking on)
            "chat_template_kwargs": {"enable_thinking": bool(thinking)}}
    if schema is not None:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}}
    last = None
    for attempt in range(retries + 1):
        key = open(KEY_FILE, encoding="utf-8").read().strip()
        req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(),
                                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        key = None
        t = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                r = json.load(resp)
            msg = r["choices"][0]["message"]
            return {"provider": "NVIDIA_NIM", "endpoint": ENDPOINT, "enable_thinking": bool(thinking), "max_tokens": max_tokens, "model": r.get("model", MODEL), "response_id": r.get("id"),
                    "content": msg.get("content"), "reasoning_chars": len(msg.get("reasoning_content") or msg.get("reasoning") or ""),
                    "finish_reason": r["choices"][0].get("finish_reason"), "usage": r.get("usage"),
                    "latency_s": round(time.time() - t, 2), "attempt": attempt}
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as e:
            last = {"error": type(e).__name__, "status": getattr(e, "code", None), "attempt": attempt}
            time.sleep(2 * (attempt + 1))
    return {"provider": "NVIDIA_NIM", "model": MODEL, "failed": True, **(last or {})}


if __name__ == "__main__":
    r = chat([{"role": "user", "content": 'Return the JSON object {"ok": true, "n": 3}.'}],
             schema={"type": "object", "properties": {"ok": {"type": "boolean"}, "n": {"type": "integer"}}, "required": ["ok", "n"]},
             max_tokens=2048)
    print(json.dumps({k: r.get(k) for k in ("model", "response_id", "content", "finish_reason", "usage", "latency_s", "failed", "error", "status")}))
