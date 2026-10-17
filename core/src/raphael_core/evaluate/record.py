"""Record the evaluation set: the user reads each prompt aloud and the clips are saved as files.

    uv run python -m raphael_core.evaluate.record --prompts ../eval/dictation_prompts.txt --out ../eval/data

For every prompt, Enter starts the recording and Enter stops it. Prompt number N is saved as
`NN.wav` (16 kHz, mono, 16-bit PCM) with `NN.txt` beside it, which holds the prompt as the reference
text: edit it to what was really said if the user read something differently. A clip that is
already there is kept, so the run can be stopped and continued; the reference text is never
replaced.

The microphone is opened with sounddevice, and only while a clip is being recorded.
"""

import argparse
import logging
import os
import sys
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import numpy as np

from raphael_core.evaluate import use_utf8_output

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
# A take shorter than this is a slip of the finger, not speech.
MIN_SECONDS = 0.3


class RecordingError(Exception):
    """The microphone could not be opened or read."""


class Recorder(Protocol):
    def start(self) -> None:
        """Begin capturing."""
        ...

    def stop(self) -> np.ndarray:
        """Stop capturing; the audio since `start` as 1-D int16 samples at 16 kHz."""
        ...


class SoundDeviceRecorder:
    """Captures from the microphone with sounddevice (PortAudio): 16 kHz, mono, 16-bit.

    `device` is an index or a part of a device's name; None is the system default input.
    """

    def __init__(self, device: int | str | None = None) -> None:
        try:
            import sounddevice
        except (ImportError, OSError) as exc:  # OSError: PortAudio itself is missing
            raise RecordingError(f"無法載入 sounddevice：{exc}") from exc
        self._sd = sounddevice
        self._device = device
        self._chunks: list[np.ndarray] = []
        self._stream = None

    def start(self) -> None:
        self._chunks = []
        try:
            self._stream = self._sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
                device=self._device,
                callback=self._on_audio,
            )
            self._stream.start()
        except (self._sd.PortAudioError, ValueError) as exc:
            self._stream = None
            raise RecordingError(f"無法開啟麥克風：{exc}") from exc

    def _on_audio(self, indata, frames, time, status) -> None:
        if status:
            logger.warning("Audio input status: %s", status)
        self._chunks.append(indata[:, 0].copy())

    def stop(self) -> np.ndarray:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except self._sd.PortAudioError as exc:  # the microphone was unplugged, say
                raise RecordingError(f"麥克風中斷了：{exc}") from exc
        if not self._chunks:
            return np.zeros(0, dtype="<i2")
        return np.concatenate(self._chunks).astype("<i2")


def load_prompts(path: Path) -> list[str]:
    """The lines of the prompts file, without blank lines and without lines that start with #."""
    lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    stripped = (line.strip() for line in lines)
    return [line for line in stripped if line and not line.startswith("#")]


def clip_name(number: int) -> str:
    """The file name without extension of prompt `number` (counted from 1): 01, 02, ..."""
    return f"{number:02d}"


def write_wav(path: Path, pcm: np.ndarray) -> None:
    """Save 1-D int16 samples as a 16 kHz mono 16-bit PCM WAV file.

    The file appears under its name only once it is complete.
    """
    path = Path(path)
    part = path.with_name(path.name + ".part")
    try:
        with wave.open(str(part), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(SAMPLE_RATE)
            writer.writeframes(np.asarray(pcm, dtype="<i2").tobytes())
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)


def _take(recorder: Recorder, ask: Callable[[str], str]) -> np.ndarray:
    """One recording: Enter has started it, and it lasts until the user presses Enter again."""
    recorder.start()
    try:
        ask("  錄音中……說完後按 Enter 停止：")
    finally:
        pcm = recorder.stop()
    return pcm


def record_session(
    prompts: list[str],
    out_dir: Path,
    *,
    recorder: Recorder,
    ask: Callable[[str], str] = input,
    say: Callable[[str], None] = print,
    overwrite: bool = False,
) -> int:
    """Walk through `prompts`, record each, and save the clips in `out_dir`. Returns how many
    clips were saved.

    Per prompt: Enter starts, "s" skips it, "q" ends the session. After a take, Enter keeps it and
    "r" records it again. A take shorter than MIN_SECONDS is refused. The end of the input, or
    Ctrl-C, ends the session; what was saved stays.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    try:
        for number, prompt in enumerate(prompts, start=1):
            wav = out_dir / f"{clip_name(number)}.wav"
            label = f"[{number}/{len(prompts)}]"
            if wav.exists() and not overwrite:
                say(f"{label} 已有 {wav.name}，略過（要重錄請加 --overwrite）")
                continue
            say(f"\n{label} 請朗讀：\n    {prompt}")
            while True:
                answer = ask("  按 Enter 開始錄音（s 跳過，q 結束）：").strip().lower()
                if answer == "q":
                    return saved
                if answer == "s":
                    break
                pcm = _take(recorder, ask)
                seconds = pcm.size / SAMPLE_RATE
                if seconds < MIN_SECONDS:
                    say(f"  太短（{seconds:.1f} 秒），請重錄。")
                    continue
                if ask(f"  錄了 {seconds:.1f} 秒。按 Enter 儲存，r 重錄：").strip().lower() == "r":
                    continue
                write_wav(wav, pcm)
                reference = wav.with_suffix(".txt")
                if not reference.exists():  # the user may have corrected it
                    reference.write_text(prompt + "\n", encoding="utf-8", newline="\n")
                say(f"  已儲存 {wav.name}")
                saved += 1
                break
    except (EOFError, KeyboardInterrupt):
        say("\n錄音中止。")
    return saved


def _device(value: str) -> int | str:
    return int(value) if value.isdigit() else value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m raphael_core.evaluate.record",
        description="Read each prompt aloud and save the recording as NN.wav with NN.txt.",
    )
    parser.add_argument("--prompts", type=Path, default=Path("../eval/dictation_prompts.txt"))
    parser.add_argument("--out", type=Path, default=Path("../eval/data"))
    parser.add_argument(
        "--device", type=_device, default=None, help="microphone: an index or part of its name"
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="record again the clips that already exist"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        prompts = load_prompts(args.prompts)
    except OSError as exc:
        print(f"讀不到提示文字檔 {args.prompts}：{exc}", file=sys.stderr)
        return 1
    if not prompts:
        print(f"提示文字檔 {args.prompts} 裡沒有任何一行。", file=sys.stderr)
        return 1
    try:
        recorder = SoundDeviceRecorder(args.device)
        print(
            f"共 {len(prompts)} 句。每句：按 Enter 開始錄音，說完再按 Enter 停止。"
            f"\n錄音存到 {args.out}。Ctrl-C 可隨時中止，下次會從沒錄的接著錄。"
        )
        saved = record_session(prompts, args.out, recorder=recorder, overwrite=args.overwrite)
    except RecordingError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"\n本次存了 {saved} 段，位置 {args.out}")
    return 0


if __name__ == "__main__":
    use_utf8_output()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    sys.exit(main())
