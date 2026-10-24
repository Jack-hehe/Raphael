"""Bounded attachment readers. File content is data, never code or instructions."""

import asyncio
import io
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Awaitable, Callable
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

from .attachments import Attachment
from .common import fit

MAX_EXTRACT_BYTES = 8 * 1024 * 1024
MAX_MEDIA_SECONDS = 300
Transcribe = Callable[[bytes, str], Awaitable[str]]
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".html", ".htm", ".csv", ".tsv", ".json", ".jsonl",
    ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".log", ".sql",
    ".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".c", ".cpp", ".h", ".hpp",
    ".cs", ".java", ".kt", ".go", ".rb", ".php", ".sh", ".ps1", ".css", ".scss",
    ".vue", ".svelte", ".dart", ".swift", ".tex", ".r", ".ipynb", ".svg",
}


class ReadableHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "template", "noscript"):
            self.hidden += 1
        if not self.hidden and tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "template", "noscript"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def decode(data: bytes) -> str | None:
    for encoding in ("utf-8-sig", "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8", "big5", "cp1252"):
        try:
            text = data.decode(encoding)
        except UnicodeError:
            continue
        if not text or sum(c < " " and c not in "\n\r\t" for c in text) / len(text) < 0.02:
            return text
    return None


def office_text(data: bytes, extension: str) -> str:
    """Read only known document parts, without extraction, relationships or external entities."""
    parts = []
    used = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = sorted(archive.infolist(), key=lambda i: i.filename)
        strings: list[str] = []
        for info in infos:
            name = info.filename
            wanted = (
                extension == ".docx" and name == "word/document.xml"
                or extension == ".pptx" and name.startswith("ppt/slides/slide") and name.endswith(".xml")
                or extension == ".xlsx" and (name == "xl/sharedStrings.xml" or name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
            )
            if not wanted:
                continue
            used += info.file_size
            if used > MAX_EXTRACT_BYTES:
                parts.append("[Remaining document parts exceed the extraction limit.]")
                break
            raw = archive.read(info)
            declarations = raw.replace(b"\x00", b"").upper()
            if b"<!DOCTYPE" in declarations or b"<!ENTITY" in declarations:
                raise ValueError("xml_entities")
            root = ET.fromstring(raw)
            if name == "xl/sharedStrings.xml":
                strings = ["".join(n.itertext()) for n in root]
                continue
            if extension == ".xlsx":
                for row in root.iter():
                    if row.tag.rsplit("}", 1)[-1] != "row":
                        continue
                    cells = []
                    for cell in row:
                        value = "".join(n.text or "" for n in cell.iter() if n.tag.rsplit("}", 1)[-1] in ("v", "t"))
                        if cell.get("t") == "s" and value.isdigit():
                            value = strings[int(value)] if int(value) < len(strings) else ""
                        cells.append(value)
                    parts.append("\t".join(cells))
            else:
                for node in root.iter():
                    local = node.tag.rsplit("}", 1)[-1]
                    if local == "t" and node.text:
                        parts.append(node.text)
                    elif local in ("p", "br", "tab"):
                        parts.append("\n")
    return "\n".join(parts)


def convert_media(attachment: Attachment) -> tuple[bytes, float | None, bool]:
    """Convert at most five minutes to mono WAV; no shell, network protocols or stderr text."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise FileNotFoundError
    extension = Path(attachment.name).suffix.lower()
    if not extension or len(extension) > 10 or not extension[1:].isalnum():
        extension = ".bin"
    with tempfile.TemporaryDirectory(prefix="raphael-media-") as directory:
        source = Path(directory) / ("input" + extension)
        target = Path(directory) / "audio.wav"
        source.write_bytes(attachment.data)
        flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        subprocess.run(
            [ffmpeg, "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe", "-format_whitelist", "mov,matroska,webm,mp3,wav,ogg,flac,aac", "-i", str(source),
             "-t", str(MAX_MEDIA_SECONDS), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target)],
            check=True, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags,
        )
        import wave
        with wave.open(str(target), "rb") as wav:
            duration = wav.getnframes() / wav.getframerate()
        return target.read_bytes(), duration, duration >= MAX_MEDIA_SECONDS - 0.1


async def read_file(attachment: Attachment, transcribe: Transcribe | None = None) -> tuple[str, dict]:
    extension = Path(attachment.name).suffix.lower()
    facts = {"name": attachment.name, "mime": attachment.mime, "size": len(attachment.data)}
    video = attachment.mime.startswith("video/") or extension in (".mp4", ".mov", ".mkv")
    audio = attachment.mime.startswith("audio/") or extension in (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".webm")
    if video or audio:
        kind = "video" if video else "audio"
        if transcribe is None:
            return kind, {**facts, "limitation": "Speech recognition is unavailable; the media was not transcribed."}
        try:
            # libsndfile can read these directly; other formats need ffmpeg's WAV conversion.
            direct = not video and extension in (".wav", ".mp3", ".ogg", ".flac")
            if direct:
                import soundfile
                with soundfile.SoundFile(io.BytesIO(attachment.data)) as clip:
                    duration = len(clip) / clip.samplerate
                if duration > MAX_MEDIA_SECONDS:
                    direct = False
            if direct:
                data, truncated = attachment.data, False
            else:
                data, duration, truncated = await asyncio.to_thread(convert_media, attachment)
            text = await transcribe(data, "auto")
            text, cut = fit(text)
            return kind, {**facts, "text": text, "duration": duration, "truncated": cut or truncated}
        except FileNotFoundError:
            return kind, {**facts, "limitation": "ffmpeg is unavailable; this media's audio could not be read."}
        except asyncio.CancelledError:
            raise
        except Exception:
            return kind, {**facts, "limitation": "The audio could not be decoded or transcribed."}
    if extension in (".docx", ".xlsx", ".pptx"):
        try:
            text = await asyncio.to_thread(office_text, attachment.data, extension)
        except (ValueError, OSError, zipfile.BadZipFile, ET.ParseError, RuntimeError):
            return "document", {**facts, "limitation": "The Office document could not be read."}
        text, cut = fit(text)
        return "document", {**facts, "text": text, "truncated": cut}
    known_text = extension in TEXT_EXTENSIONS or attachment.mime.startswith("text/") or attachment.mime in ("application/json", "application/xml", "application/yaml", "application/javascript")
    data = attachment.data[:MAX_EXTRACT_BYTES if known_text else 512]
    text = decode(data)
    if text is not None:
        if extension in (".html", ".htm") or attachment.mime == "text/html":
            html = ReadableHTML()
            html.feed(text)
            text = "".join(html.parts)
        text, cut = fit(text)
        if not known_text:
            return "file", {**facts, "sniff": text, "truncated": len(data) < len(attachment.data), "limitation": "Unknown format; only a short text sample was read."}
        return "text", {**facts, "text": text, "truncated": cut or len(data) < len(attachment.data)}
    return "file", {**facts, "limitation": "Binary format: only file metadata is available."}
