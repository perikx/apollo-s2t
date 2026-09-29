"""Offline diagnostics by default. --live explicitly records audio and uses paid APIs."""
import argparse
from pathlib import Path
import sys

from apollo_config import ConfigError, api_key, read_config

BASE = Path(__file__).resolve().parent


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Record microphone audio and call both paid APIs")
    parser.add_argument("--seconds", type=int, default=5, help="Live recording duration (1-30 seconds)")
    args = parser.parse_args(argv)
    if not 1 <= args.seconds <= 30:
        parser.error("--seconds must be between 1 and 30")
    failures = 0
    try:
        cfg, notes = read_config(BASE / "config.json")
        for note in notes:
            print(note)
        if not api_key(cfg):
            raise ConfigError("Missing OpenRouter key. Run setup.bat.")
        print("[OK] Configuration and API key present (not yet authenticated).")
        print("Speech:", cfg["openrouter_stt"]["model"])
        print("Rewrite:", cfg["smoothing"]["model"])
    except (ConfigError, OSError) as exc:
        print("[FAIL]", exc)
        return 1
    try:
        import sounddevice as sd
        audio = cfg["audio"]
        sd.check_input_settings(device=audio["device"], channels=audio["channels"],
                                samplerate=audio["samplerate"], dtype="int16")
        for index, device in enumerate(sd.query_devices()):
            if device["max_input_channels"]:
                print(f"  Input {index}: {device['name']}")
        print("[OK] Configured audio input accepts the sample rate and channel count.")
    except Exception:
        print("[FAIL] Audio input unavailable. Check installation, device and Windows microphone permissions.")
        failures += 1
    if not args.live:
        print("Offline checks only. No API request, recording or clipboard change was made.")
        print("For a paid end-to-end test: python selftest.py --live")
        return int(bool(failures))
    if failures:
        return 1
    try:
        from apollo import to_wav_bytes, PROMPTS
        from apollo_api import transcribe_openrouter, smooth
        print(f"Speak now for {args.seconds} seconds. Audio will be sent to OpenRouter.")
        data = sd.rec(int(audio["samplerate"] * args.seconds), samplerate=audio["samplerate"],
                      channels=audio["channels"], dtype="int16", device=audio["device"], blocking=True)
        text = transcribe_openrouter(to_wav_bytes(data, audio["samplerate"], audio["channels"]),
                                     cfg["openrouter_stt"], api_key(cfg))
        if not text:
            raise ValueError("No speech recognized")
        print("[OK] Transcription:", text)
        rewrite = smooth(text, PROMPTS["polish"], dict(cfg["smoothing"], api_key=api_key(cfg)))
        if not rewrite:
            raise ValueError("No rewrite returned")
        print("[OK] Rewrite:", rewrite)
        print("No text was pasted and no audio was saved. This is a smoke test, not an accuracy benchmark.")
    except Exception as exc:
        from apollo_api import http_error_hint
        import requests
        message = http_error_hint("Live test", exc) if isinstance(exc, requests.RequestException) else "No complete audio/transcription/rewrite result."
        print("[FAIL]", message)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
