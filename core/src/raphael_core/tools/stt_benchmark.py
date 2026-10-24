"""Opt-in, PAID OpenRouter STT benchmark. Never run automatically.

Set OPENROUTER_API_KEY, then run:
python -m raphael_core.tools.stt_benchmark short.wav --models MODEL MODEL --confirm-paid
Sends identical WAV bytes to each model; never reads/writes app settings or prints transcripts.
"""

import argparse
import asyncio
import base64
import os
import time
import wave
from pathlib import Path

from raphael_core.brain.openrouter import OpenRouterClient
from raphael_core.tools.dictation_timings import percentile


async def benchmark(client, fixtures: list[tuple[str, bytes]], models: list[str], repeats: int) -> list[dict]:
    rows = []
    for name, data in fixtures:
        audio = base64.b64encode(data).decode("ascii")
        for model in models:
            latencies, errors, cost = [], 0, 0.0
            for _ in range(repeats):
                started = time.perf_counter()
                try:
                    result = await client.transcribe(model=model, audio_b64=audio,
                                                    language=None, languages=[], hints=[], timeout_s=30)
                except Exception:
                    errors += 1
                else:
                    latencies.append((time.perf_counter() - started) * 1000)
                    cost += result.cost_usd
            rows.append({"fixture": name, "model": model, "ok": len(latencies), "errors": errors,
                         "p50_ms": percentile(latencies, .5), "p90_ms": percentile(latencies, .9),
                         "cost_usd": cost})
    return rows


async def run(args, fixtures):
    client = OpenRouterClient(lambda: os.environ.get("OPENROUTER_API_KEY"))
    try:
        # Warm once outside the timed calls. Model order is explicit and stays as requested.
        await client.warm_connection()
        rows = await benchmark(client, fixtures, args.models, args.repeats)
        for row in rows:
            print(" | ".join(f"{key}={value}" for key, value in row.items()))
    finally:
        await client.aclose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wavs", type=Path, nargs="+")
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--confirm-paid", action="store_true")
    args = parser.parse_args()
    if not args.confirm_paid:
        parser.error("This sends paid requests. Pass --confirm-paid only when you intend to run it.")
    if not os.environ.get("OPENROUTER_API_KEY"):
        parser.error("Set OPENROUTER_API_KEY in your environment; never put it in command arguments.")
    if args.repeats < 1:
        parser.error("repeats must be positive")
    fixtures = []
    for path in args.wavs:
        with wave.open(str(path), "rb") as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16_000):
                parser.error("Fixtures must be 16 kHz mono, 16-bit PCM WAV files")
            if not 0 < wav.getnframes() / wav.getframerate() <= 15:
                parser.error("Fixtures must be short (0 < seconds <= 15)")
        fixtures.append((path.name, path.read_bytes()))
    asyncio.run(run(args, fixtures))


if __name__ == "__main__":
    main()
