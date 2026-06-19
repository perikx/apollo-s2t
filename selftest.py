"""
Self-test for Apollo s2t.
Checks: imports, audio devices, Deepgram key, OpenRouter key + model.
Run:  .venv\\Scripts\\python.exe selftest.py
"""
import io
import json
import os
import wave

import numpy as np
import requests
import sounddevice as sd

BASE = os.path.dirname(os.path.abspath(__file__))
cfg = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))

ok = "[ OK ]"
bad = "[FAIL]"


def section(t):
    print("\n" + "=" * 60 + "\n " + t + "\n" + "=" * 60)


# 1) Audiogeraete -----------------------------------------------------------
section("Audio input devices")
try:
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            mark = "  <- default" if i == sd.default.device[0] else ""
            print(f"  [{i}] {d['name']}{mark}")
    print(ok, "sounddevice works")
except Exception as e:
    print(bad, "sounddevice:", e)

# 2) Deepgram ---------------------------------------------------------------
section("Deepgram (STT)")
try:
    dg = cfg["deepgram"]
    # 0.3 s of silence as a valid WAV
    sr = cfg["audio"]["samplerate"]
    silence = np.zeros(int(sr * 0.3), dtype=np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(silence.tobytes())

    params = {"model": dg["model"], "smart_format": "true"}
    if dg.get("language"):
        params["language"] = dg["language"]
    r = requests.post(
        "https://api.deepgram.com/v1/listen",
        params=params,
        headers={"Authorization": f"Token {dg['api_key']}", "Content-Type": "audio/wav"},
        data=buf.getvalue(),
        timeout=30,
    )
    if r.status_code == 200:
        print(ok, f"Deepgram key valid, model '{dg['model']}' accepted (HTTP 200).")
    else:
        print(bad, f"HTTP {r.status_code}: {r.text[:300]}")
except Exception as e:
    print(bad, "Deepgram:", e)

# 3) OpenRouter -------------------------------------------------------------
section("OpenRouter (LLM for F9/F10)")
try:
    sm = cfg["smoothing"]
    r = requests.post(
        sm["base_url"],
        headers={
            "Authorization": f"Bearer {sm['api_key']}",
            "Content-Type": "application/json",
            "X-Title": "Apollo-s2t-Selftest",
        },
        json={
            "model": sm["model"],
            "messages": [{"role": "user", "content": "Reply with only the word: OK"}],
            "max_tokens": 200,
        },
        timeout=40,
    )
    if r.status_code == 200:
        content = (r.json()["choices"][0]["message"].get("content") or "").strip()
        if content:
            print(ok, f"OpenRouter key valid, model '{sm['model']}' replies: {content!r}")
        else:
            print(bad, f"Model '{sm['model']}' returned empty content (None). "
                       "Try a different slug in config.json -> smoothing.model.")
    else:
        print(bad, f"HTTP {r.status_code}: {r.text[:400]}")
        print("      -> check the model slug at https://openrouter.ai/models")
except Exception as e:
    print(bad, "OpenRouter:", e)

# 4) Deepgram Streaming -----------------------------------------------------
section("Deepgram streaming (WebSocket)")
try:
    import time
    from apollo import DeepgramLive

    sr = cfg["audio"]["samplerate"]
    live = DeepgramLive(cfg["deepgram"], sr, 1)
    live.open_async()
    # send ~1 s of silence in 50 ms chunks (tests send_binary + flush)
    chunk = np.zeros(int(sr * 0.05), dtype=np.int16).tobytes()
    for _ in range(20):
        live.send(chunk)
        time.sleep(0.01)
    text = live.finish(timeout=4)
    if live.error is not None:
        print(bad, "streaming connection:", live.error)
    else:
        print(ok, "streaming handshake + finalize ok "
                  f"(linear16 @ {sr} Hz accepted). Transcript (silence): {text!r}")
except Exception as e:
    print(bad, "Streaming:", e)

print("\nDone.")
