// Shared presentation for composer files and the stored copies in a sent message.
import { t } from "./i18n";
import type { TurnAttachment } from "./types";
import { esc, ico, iconButton } from "./ui";

const EXTENSIONS: Record<string, string> = {
  png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", webp: "image/webp", gif: "image/gif", avif: "image/avif", bmp: "image/bmp",
  pdf: "application/pdf", txt: "text/plain", md: "text/markdown", html: "text/html", htm: "text/html", csv: "text/csv", json: "application/json",
  mp4: "video/mp4", mov: "video/quicktime", mkv: "video/x-matroska", webm: "video/webm",
  mp3: "audio/mpeg", wav: "audio/wav", m4a: "audio/mp4", ogg: "audio/ogg", flac: "audio/flac",
};
export function fileMime(file: { name: string; type: string }): string {
  const type = file.type.trim().toLowerCase().split(";")[0];
  if (type === "image/jpg") return "image/jpeg";
  return type && type !== "application/octet-stream" ? type : EXTENSIONS[file.name.split(".").pop()?.toLowerCase() ?? ""] ?? "application/octet-stream";
}
export function fileKind(mime: string): "image" | "video" | "audio" | "file" {
  // SVG is a document, not trusted image markup.
  if (/^image\/(png|jpeg|webp|gif|avif|bmp)$/.test(mime)) return "image";
  if (mime.startsWith("video/")) return "video";
  if (mime.startsWith("audio/")) return "audio";
  return "file";
}
export function fileSize(size?: number): string {
  if (size === undefined) return "";
  return size >= 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)} MB` : size >= 1024 ? `${Math.ceil(size / 1024)} KB` : `${size} B`;
}
export function fileIcon(file: Pick<TurnAttachment, "name" | "mime">): string {
  const kind = fileKind(file.mime);
  if (kind !== "file") return ico(kind === "audio" ? "mic" : kind === "video" ? "play" : "image");
  const ext = file.name.split(".").pop()?.toUpperCase().slice(0, 6) ?? "";
  return `<span class="attachment-icon">${ico(/^(DOCX|XLSX|PPTX|PDF)$/.test(ext) ? "book" : "file")}<small>${esc(ext)}</small></span>`;
}
export function sentFileHTML(file: TurnAttachment): string {
  const kind = fileKind(file.mime);
  const id = typeof file.id === "string" && /^[a-f0-9]{32}\.[a-z0-9]{1,12}$/.test(file.id) ? file.id : "";
  const source = file.preview && /^data:(image\/(png|jpeg|webp|gif|avif|bmp)|audio\/[a-z0-9.+-]+|video\/[a-z0-9.+-]+);base64,[A-Za-z0-9+/=]*$/.test(file.preview) ? file.preview : "";
  const attrs = id ? ` data-attachment="${esc(id)}"` : "";
  const src = source ? ` src="${esc(source)}"` : "";
  const open = id ? iconButton("external", t("agent.openFile"), ` data-act="attachment-open" data-id="${esc(id)}"`, "attachment-open") : "";
  if ((id || source) && kind === "image") return `<div class="me-file attachment-image"><button type="button" class="btn attachment-picture" data-act="attachment-image"${attrs} aria-label="${esc(file.name)}"><img${src} alt="${esc(file.name)}"${attrs}></button></div>`;
  if ((id || source) && kind === "video") return `<div class="me-file attachment-video"><video${src}${attrs} controls preload="metadata" playsinline aria-label="${esc(file.name)}"></video>${open}</div>`;
  if ((id || source) && kind === "audio") return `<div class="me-file attachment-audio"><audio${src}${attrs} preload="metadata"></audio>` +
    iconButton("play", t("agent.cmd.playPause"), ' data-audio-toggle aria-pressed="false"', "attachment-play") +
    `<span class="attachment-audio-body"><span class="attachment-name"><span class="me-file-t">${esc(file.name)}</span></span><input type="range" min="0" max="100" step="0.1" value="0" disabled data-audio-progress aria-label="${esc(t("agent.audioProgress"))}"><small data-audio-time>0:00 / 0:00</small></span>${open}</div>`;
  const ext = file.name.includes(".") ? file.name.split(".").pop()!.toUpperCase() : "";
  const details = [fileSize(file.size), ext].filter(Boolean).join(" · ");
  return `<button type="button" class="btn me-file attachment-file"${id ? ` data-act="attachment-open" data-id="${esc(id)}"` : " disabled"} aria-label="${esc(t("agent.openFile"))}: ${esc(file.name)}">${fileIcon(file)}<span class="attachment-name"><span class="me-file-t">${esc(file.name)}</span><small>${esc(details)}</small></span></button>`;
}

/** A depth counter keeps child crossings from dismissing the thread's drop target. */
export function mountFileDrop(target: HTMLElement, overlay: HTMLElement, add: (files: File[]) => void): () => void {
  let depth = 0;
  const reset = (): void => { depth = 0; overlay.hidden = true; };
  const hasFiles = (e: DragEvent): boolean => Array.from(e.dataTransfer?.types ?? []).includes("Files");
  const enter = (e: DragEvent): void => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth++;
    overlay.hidden = false;
  };
  const over = (e: DragEvent): void => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
    overlay.hidden = false;
  };
  const leave = (e: DragEvent): void => {
    if (!hasFiles(e) && !depth) return;
    if (e.relatedTarget instanceof Node && target.contains(e.relatedTarget)) { depth = Math.max(0, depth - 1); return; }
    if (--depth <= 0) reset();
  };
  const drop = (e: DragEvent): void => {
    const files = Array.from(e.dataTransfer?.files ?? []);
    if (files.length) { e.preventDefault(); add(files); }
    reset();
  };
  target.addEventListener("dragenter", enter);
  target.addEventListener("dragover", over);
  target.addEventListener("dragleave", leave);
  target.addEventListener("drop", drop);
  window.addEventListener("dragend", reset);
  window.addEventListener("drop", reset);
  window.addEventListener("blur", reset);
  return reset;
}
