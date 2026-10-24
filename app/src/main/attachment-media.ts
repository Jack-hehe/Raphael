// Stored media is loaded only near the viewport. Object URLs belong to this mounted thread.
import { fileKind } from "./attachments";
import { t } from "./i18n";
import { ico, toaster } from "./ui";

type Loaded = { url: string; size: number };
export function createAttachmentMedia(root: HTMLElement, read: (id: string) => Promise<{ mime: string; data: string }>) {
  const loaded = new Map<string, Loaded>();
  const pending = new Set<string>();
  let epoch = 0;
  const players = new Set<HTMLMediaElement>();
  const bound = new WeakSet<HTMLAudioElement>();
  const clock = (seconds: number): string => {
    const value = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
    return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, "0")}`;
  };
  const bindAudio = (audio: HTMLAudioElement): void => {
    if (bound.has(audio)) return;
    const pill = audio.closest(".attachment-audio");
    const toggle = pill?.querySelector<HTMLButtonElement>("[data-audio-toggle]");
    const progress = pill?.querySelector<HTMLInputElement>("[data-audio-progress]");
    const time = pill?.querySelector<HTMLElement>("[data-audio-time]");
    if (!toggle || !progress || !time) return;
    bound.add(audio);
    const update = (): void => {
      const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
      progress.disabled = duration <= 0;
      progress.value = String(duration > 0 ? audio.currentTime / duration * 100 : 0);
      progress.setAttribute("aria-valuetext", `${clock(audio.currentTime)} / ${clock(duration)}`);
      time.textContent = `${clock(audio.currentTime)} / ${clock(duration)}`;
      toggle.innerHTML = ico(audio.paused ? "play" : "pause");
      toggle.setAttribute("aria-pressed", String(!audio.paused));
    };
    toggle.addEventListener("click", () => {
      if (!audio.paused) audio.pause();
      else void audio.play().catch(() => { update(); toaster.info(t("agent.fileUnavailable")); });
    });
    progress.addEventListener("input", () => {
      if (Number.isFinite(audio.duration) && audio.duration > 0) audio.currentTime = Number(progress.value) / 100 * audio.duration;
      update();
    });
    for (const event of ["loadedmetadata", "durationchange", "timeupdate", "play", "pause", "ended", "error"]) audio.addEventListener(event, update);
    update();
  };
  const apply = (id: string, url: string): void => {
    for (const node of root.querySelectorAll<HTMLImageElement | HTMLMediaElement>(`img[data-attachment="${id}"],video[data-attachment="${id}"],audio[data-attachment="${id}"]`)) {
      if (node.getAttribute("src") !== url) node.src = url;
    }
  };
  const load = async (node: Element): Promise<void> => {
    const id = (node as HTMLElement).dataset.attachment;
    if (!id || !/^[a-f0-9]{32}\.[a-z0-9]{1,12}$/.test(id)) return;
    const cached = loaded.get(id);
    if (cached) { apply(id, cached.url); return; }
    if (pending.has(id)) return;
    const generation = epoch;
    pending.add(id);
    try {
      const file = await read(id);
      if (generation !== epoch || fileKind(file.mime) === "file" || file.data.length > 70_000_000) return;
      const binary = atob(file.data);
      const bytes = Uint8Array.from(binary, c => c.charCodeAt(0));
      // Bound decoded media to 100 MB; older nodes can load again when scrolled into view.
      while ([...loaded.values()].reduce((sum, item) => sum + item.size, 0) + bytes.length > 100 * 1024 * 1024 && loaded.size) {
        const [old, entry] = loaded.entries().next().value!;
        URL.revokeObjectURL(entry.url);
        loaded.delete(old);
        for (const media of root.querySelectorAll(`[data-attachment="${old}"][src]`)) media.removeAttribute("src");
      }
      const url = URL.createObjectURL(new Blob([bytes], { type: file.mime }));
      loaded.set(id, { url, size: bytes.length });
      apply(id, url);
    } catch {
      // A missing stored copy leaves its name visible, without exposing backend details.
    } finally { if (generation === epoch) pending.delete(id); }
  };
  const observer = typeof IntersectionObserver === "function" ? new IntersectionObserver(entries => {
    for (const entry of entries) if (entry.isIntersecting) void load(entry.target.querySelector("audio") ?? entry.target);
  }, { root, rootMargin: "240px" }) : null;
  return {
    sync(): void {
      observer?.disconnect();
      for (const player of players) if (!root.contains(player)) { player.pause(); players.delete(player); }
      for (const player of root.querySelectorAll<HTMLMediaElement>("audio,video")) {
        players.add(player);
        if (player instanceof HTMLAudioElement) bindAudio(player);
      }
      const ids = new Set([...root.querySelectorAll<HTMLElement>("[data-attachment]")].map(node => node.dataset.attachment));
      for (const [id, entry] of loaded) if (!ids.has(id)) { URL.revokeObjectURL(entry.url); loaded.delete(id); }
      for (const node of root.querySelectorAll("img[data-attachment],video[data-attachment],audio[data-attachment]")) {
        // Custom audio controls hide the native element; watch their visible pill instead.
        if (observer) observer.observe(node.closest(".attachment-audio") ?? node);
        else void load(node);
      }
    },
    source(button: HTMLElement): string | undefined {
      const id = button.dataset.attachment;
      if (id) return loaded.get(id)?.url;
      const src = button.querySelector("img")?.getAttribute("src");
      return src && /^data:image\/(png|jpeg|webp|gif|avif|bmp);base64,/.test(src) ? src : undefined;
    },
    reset(): void {
      epoch++;
      for (const player of players) player.pause();
      players.clear();
      observer?.disconnect();
      pending.clear();
      for (const entry of loaded.values()) URL.revokeObjectURL(entry.url);
      loaded.clear();
    },
  };
}
