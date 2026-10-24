"""Offline latency harness using real dictation/brain services and scripted providers.

python -m raphael_core.tools.dictation_latency --trials 5
The legacy comparison models sequential cloud + mandatory cleanup; it is not a historic binary.
Reported wall times are for fake providers only, never a prediction of live provider performance.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from dataclasses import dataclass

import numpy as np

from raphael_core.brain.cleanup import CleanupService
from raphael_core.brain.db import connect
from raphael_core.brain.dictionary import DictionaryStore
from raphael_core.brain.openrouter import Completion, Transcription
from raphael_core.brain.secrets import MemoryKeyStore
from raphael_core.brain.service import Brain
from raphael_core.brain.settings import SettingsStore
from raphael_core.brain.spend import SpendTracker
from raphael_core.device.cloud_stt import CloudSttEngine
from raphael_core.device.dictation import DictationService
from raphael_core.router import Router
from raphael_core.tools.dictation_timings import percentile


class FakeProvider:
    """Cloud = 4 seconds, cleanup = 2 seconds, scaled down to keep the harness fast."""

    def __init__(self, text: str, scale: float):
        self.text, self.scale = text, scale
        self.cleanup_calls = 0
        self.cloud_calls = 0

    async def transcribe(self, **kwargs):
        self.cloud_calls += 1
        await asyncio.sleep(4 * self.scale)
        return Transcription(self.text, "fake/cloud", 0, 1, 0, 0, 0)

    async def complete_json(self, **kwargs):
        self.cleanup_calls += 1
        await asyncio.sleep(2 * self.scale)
        return Completion({"text": self.text}, "fake/cleanup", 0, 0, 0, 0)


class TailRecorder:
    """One short utterance delivered by stop(), with no device or model access."""

    error = None

    def __init__(self, callback):
        self.callback = callback

    def start(self):
        pass

    def stop(self):
        self.callback(np.full(16_000, .1, dtype=np.float32))


class TailSegmenter:
    speech_seconds = 1.0
    silence_seconds = 0.0

    def __init__(self):
        self.samples = []

    def accept(self, samples):
        self.samples.append(samples)
        return []

    def flush(self):
        samples, self.samples = self.samples, []
        return samples


@dataclass
class Measurement:
    wall_ms: float
    cleanup_calls: int
    cloud_calls: int
    stages: list[str]


async def measure(*, legacy: bool, local: bool = False, scale: float = .02,
                  text: str = "Hello world.") -> Measurement:
    conn, rpc = connect(":memory:"), Router()
    provider, keys = FakeProvider(text, scale), MemoryKeyStore()
    keys.set("fake-key")
    brain = Brain(conn, provider, keys)
    brain.register(rpc)
    loop = asyncio.get_running_loop()
    stages: list[str] = []
    rpc.set_notifier(lambda method, params: stages.append(params["stage"]) if method == "dictation.progress" else None)

    def call(method, params, timeout):
        future = asyncio.run_coroutine_threadsafe(rpc.call(method, params), loop)
        try:
            return future.result(timeout)
        except BaseException:
            future.cancel()
            raise

    class Local:
        name = "fake/local"

        def transcribe(self, samples, **kwargs):
            time.sleep(.5 * scale)
            return text

    engine = CloudSttEngine(call, fallback=Local, warned=set(), race_local=local)
    service = DictationService(rpc, recorder_factory=TailRecorder,
                              segmenter_factory=TailSegmenter, engine_provider=lambda: engine)
    service.register(rpc)
    try:
        if legacy:
            # Explicitly model the old sequential path with the same cloud and cleanup fakes.
            cleanup = CleanupService(provider, SettingsStore(conn), DictionaryStore(conn),
                                     SpendTracker(conn), has_key=lambda: True)
            started = time.perf_counter()
            heard = await asyncio.to_thread(engine.transcribe, np.full(16_000, .1, dtype=np.float32))
            result = await cleanup.clean(heard, audio_seconds=1)
            assert result.text == text
        else:
            await rpc.call("dictation.start", {"session_id": "harness"})
            started = time.perf_counter()
            result = await rpc.call("dictation.stop", {"session_id": "harness"})
            assert result["text"] == text
        wall = (time.perf_counter() - started) * 1000
        # Let the paid-equivalent losing fake cloud call finish before closing its stores.
        await asyncio.sleep(4 * scale)
        return Measurement(wall, provider.cleanup_calls, provider.cloud_calls, stages)
    finally:
        await service.aclose()
        await brain.aclose()
        conn.close()


async def run(trials: int, scale: float) -> None:
    print(f"Fake-provider wall times; scale={scale}. Cloud=4s, cleanup=2s, local=0.5s before scaling.")
    for name, options in (
        ("modeled legacy sequential", {"legacy": True}),
        ("short clean, cloud only", {"legacy": False}),
        ("short clean, local race", {"legacy": False, "local": True}),
        ("needs cleanup, local race", {"legacy": False, "local": True, "text": "Hello world"}),
    ):
        values = [await measure(scale=scale, **options) for _ in range(trials)]
        walls = [v.wall_ms for v in values]
        print(f"{name}: n={trials} p50={percentile(walls, .5):.1f}ms p90={percentile(walls, .9):.1f}ms "
              f"cleanup_calls={sum(v.cleanup_calls for v in values)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--scale", type=float, default=.02)
    args = parser.parse_args()
    if args.trials < 1 or args.scale <= 0:
        parser.error("trials and scale must be positive")
    asyncio.run(run(args.trials, args.scale))


if __name__ == "__main__":
    main()
