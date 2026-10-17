"""Compare speech-to-text engines on the user's own recordings.

    uv run python -m raphael_core.evaluate.stt --data ../eval/data --engines qwen3-asr,sensevoice

Every `NN.wav` in the data folder is decoded by each engine and compared with the reference text in
`NN.txt`. The results are printed and written to `stt_results.csv` in the data folder, one row per
engine: the mean character error rate (see metrics.py; lower is better), the real-time factor (the
time spent decoding divided by the length of the audio; below 0.3 keeps a short dictation inside
the 2 s budget) and the mean seconds per clip. `stt_clips.csv` beside it has one row per engine and
clip, with what the engine heard, to see where the mistakes are.

Loading a model is not counted, and neither is one untimed decoding of the first clip that comes
first, which absorbs the one-off start-up cost of the runtime. A model that is not downloaded yet is
downloaded first.
"""

import argparse
import csv
import logging
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from raphael_core.device.models import ModelDownloadError, ModelManager
from raphael_core.device.stt import SAMPLE_RATE, create_engine, read_wav
from raphael_core.evaluate import use_utf8_output
from raphael_core.evaluate.metrics import cer
from raphael_core.paths import models_dir

# The engines take 20 seconds of speech at most per call.
MAX_SEGMENT_SECONDS = 20.0
# A longer clip is cut at its quietest moment within this many seconds before the limit.
_CUT_SEARCH_SECONDS = 4.0
_CUT_FRAME = SAMPLE_RATE // 10  # the loudness is measured over 100 ms
RTF_TARGET = 0.3


@dataclass(frozen=True)
class Clip:
    name: str  # "01"
    wav: Path
    reference: str


@dataclass(frozen=True)
class ClipResult:
    engine: str
    clip: str
    reference: str
    hypothesis: str
    cer: float
    audio_seconds: float
    seconds: float  # spent decoding


@dataclass(frozen=True)
class EngineSummary:
    engine: str
    clips: int
    mean_cer: float
    rtf: float  # total decoding time / total audio time
    mean_seconds: float  # per clip


def find_clips(data_dir: Path) -> tuple[list[Clip], list[str]]:
    """The clips in `data_dir` (every `NN.wav` that has a `NN.txt` with text in it), in name
    order, and the names of the recordings that have no usable reference text."""
    clips: list[Clip] = []
    unusable: list[str] = []
    for wav in sorted(Path(data_dir).glob("*.wav")):
        reference_file = wav.with_suffix(".txt")
        reference = (
            reference_file.read_text(encoding="utf-8-sig").strip()
            if reference_file.is_file()
            else ""
        )
        if reference:
            clips.append(Clip(wav.stem, wav, reference))
        else:
            unusable.append(wav.stem)
    return clips, unusable


def split_long(samples: np.ndarray, max_seconds: float = MAX_SEGMENT_SECONDS) -> list[np.ndarray]:
    """`samples` in pieces of `max_seconds` at most, for the engines to decode one by one.

    A cut falls in the quietest 100 ms of the last few seconds before the limit, where there is
    most likely a pause between words. Joined again, the pieces are the whole.
    """
    limit = round(max_seconds * SAMPLE_RATE)
    search = round(_CUT_SEARCH_SECONDS * SAMPLE_RATE)
    pieces: list[np.ndarray] = []
    start = 0
    while samples.size - start > limit:
        first = start + limit - search
        window = samples[first : start + limit]
        frames = window[: window.size // _CUT_FRAME * _CUT_FRAME].reshape(-1, _CUT_FRAME)
        quietest = int(np.argmin((frames.astype(np.float64) ** 2).mean(axis=1)))
        cut = first + quietest * _CUT_FRAME + _CUT_FRAME // 2
        pieces.append(samples[start:cut])
        start = cut
    pieces.append(samples[start:])
    return pieces


def evaluate_engine(
    engine,
    clips: list[Clip],
    *,
    clock: Callable[[], float] = time.perf_counter,
    on_clip: Callable[[ClipResult], None] | None = None,
) -> list[ClipResult]:
    """Decode every clip with `engine`, timing each with two readings of `clock`."""
    if not clips:
        return []
    # Not timed: the first decoding of a process also sets up the runtime.
    engine.transcribe(split_long(read_wav(clips[0].wav))[0])
    results = []
    for clip in clips:
        samples = read_wav(clip.wav)
        pieces = split_long(samples)
        started = clock()
        texts = [engine.transcribe(piece) for piece in pieces]
        seconds = clock() - started
        hypothesis = " ".join(text for text in texts if text)
        result = ClipResult(
            engine=engine.name,
            clip=clip.name,
            reference=clip.reference,
            hypothesis=hypothesis,
            cer=cer(clip.reference, hypothesis),
            audio_seconds=samples.size / SAMPLE_RATE,
            seconds=seconds,
        )
        results.append(result)
        if on_clip is not None:
            on_clip(result)
    return results


def summarize(engine: str, results: list[ClipResult]) -> EngineSummary:
    if not results:
        raise ValueError("No results to summarize")
    audio = sum(r.audio_seconds for r in results)
    spent = sum(r.seconds for r in results)
    return EngineSummary(
        engine=engine,
        clips=len(results),
        mean_cer=sum(r.cer for r in results) / len(results),
        rtf=spent / audio if audio else 0.0,
        mean_seconds=spent / len(results),
    )


def write_summary_csv(path: Path, summaries: list[EngineSummary]) -> None:
    with Path(path).open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["engine", "clips", "mean_cer", "rtf", "mean_seconds"])
        for s in summaries:
            writer.writerow(
                [s.engine, s.clips, f"{s.mean_cer:.4f}", f"{s.rtf:.4f}", f"{s.mean_seconds:.3f}"]
            )


def write_clips_csv(path: Path, results: list[ClipResult]) -> None:
    with Path(path).open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            ["engine", "clip", "audio_seconds", "seconds", "cer", "reference", "hypothesis"]
        )
        for r in results:
            writer.writerow(
                [
                    r.engine,
                    r.clip,
                    f"{r.audio_seconds:.2f}",
                    f"{r.seconds:.3f}",
                    f"{r.cer:.4f}",
                    r.reference,
                    r.hypothesis,
                ]
            )


def _download_progress(name: str) -> Callable[[int, int], None]:
    """A progress callback for the model manager that reports every tenth of a download."""
    last = -1

    def report(done: int, total: int) -> None:
        nonlocal last
        if total <= 0 or done * 10 // total == last:
            return
        last = done * 10 // total
        print(f"  下載 {name}：{done / 1e6:.0f} / {total / 1e6:.0f} MB", file=sys.stderr)

    return report


def _show_clip(r: ClipResult) -> None:
    print(f"  {r.clip}  CER {r.cer:6.1%}  {r.seconds:5.2f} 秒（錄音 {r.audio_seconds:.1f} 秒）")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m raphael_core.evaluate.stt",
        description="Compare speech-to-text engines on the recordings in the data folder.",
    )
    parser.add_argument("--data", type=Path, default=Path("../eval/data"))
    parser.add_argument(
        "--engines",
        default="qwen3-asr,sensevoice",
        help="comma-separated: qwen3-asr, sensevoice, breeze-asr-25 (a download of about 1.8 GB)",
    )
    parser.add_argument("--threads", type=int, default=4, help="CPU threads per engine")
    parser.add_argument(
        "--models-dir", type=Path, default=None, help="where the models are (default: the app's)"
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    models=None,
    engine_factory=create_engine,
) -> int:
    args = _parser().parse_args(argv)
    names = list(dict.fromkeys(n.strip() for n in args.engines.split(",") if n.strip()))
    if not names:
        print("--engines 沒有指定任何引擎。", file=sys.stderr)
        return 1
    clips, unusable = find_clips(args.data)
    if unusable:
        print(
            f"以下錄音沒有對應的參考文字（NN.txt 不存在或是空的），已略過：{', '.join(unusable)}",
            file=sys.stderr,
        )
    if not clips:
        print(
            f"{args.data} 裡沒有可用的錄音（需要成對的 NN.wav 與 NN.txt）。"
            "請先執行：uv run python -m raphael_core.evaluate.record",
            file=sys.stderr,
        )
        return 1
    if models is None:
        models = ModelManager(args.models_dir or models_dir())

    print(f"{len(clips)} 段錄音。")
    summaries: list[EngineSummary] = []
    all_results: list[ClipResult] = []
    failed = False
    for name in names:
        try:
            models.ensure(name, _download_progress(name))
            loading = time.perf_counter()
            engine = engine_factory(name, models, args.threads)
            print(f"\n{name}：模型載入 {time.perf_counter() - loading:.1f} 秒，開始辨識……")
            results = evaluate_engine(engine, clips, on_clip=_show_clip)
        except (ModelDownloadError, FileNotFoundError, ValueError) as exc:
            print(f"{name}：無法評估，{exc}", file=sys.stderr)
            failed = True
            continue
        summaries.append(summarize(name, results))
        all_results.extend(results)
        del engine

    if summaries:
        print("\n結果（CER 越低越好；RTF 低於 0.3 才能讓短句在 2 秒內完成）：")
        for s in summaries:
            verdict = "低於" if s.rtf < RTF_TARGET else "高於"
            print(
                f"- {s.engine}：平均 CER {s.mean_cer:.1%}，RTF {s.rtf:.2f}"
                f"（{verdict} {RTF_TARGET}），每段平均 {s.mean_seconds:.2f} 秒（{s.clips} 段）"
            )
        write_summary_csv(args.data / "stt_results.csv", summaries)
        write_clips_csv(args.data / "stt_clips.csv", all_results)
        print(f"\n已寫入 {args.data / 'stt_results.csv'} 與 {args.data / 'stt_clips.csv'}")
    return 1 if failed or not summaries else 0


if __name__ == "__main__":
    use_utf8_output()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    sys.exit(main())
