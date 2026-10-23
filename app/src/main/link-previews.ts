import { messageUrl, messageUrls, shortUrl } from "./message-content";
import { esc } from "./ui";

export interface LinkPreview {
  url: string;
  site: string;
  title: string;
  description: string;
  thumbnail: string | null;
}

function contents(url: string, preview: LinkPreview | null = null): string {
  // Core supplies bounded raster data only. Never load a remote image from a chat message.
  const thumbnail = preview?.thumbnail;
  const image = typeof thumbnail === "string" && /^data:image\/(?:png|jpeg|gif|webp|avif);base64,[A-Za-z0-9+/=]+$/.test(thumbnail)
    ? `<img class="preview-image" src="${esc(thumbnail)}" alt="" width="80" height="80">` : "";
  return image + `<span class="preview-meta"><span class="preview-site">${esc(preview?.site || new URL(url).host)}</span>` +
    `<span class="preview-title">${esc(preview?.title || shortUrl(url))}</span>` +
    `<span class="preview-description">${esc(preview?.description || new URL(url).host)}</span></span>`;
}

export function previewCardsHTML(text: string): string {
  const urls = messageUrls(text).slice(0, 3);
  return urls.length ? `<div class="preview-cards">${urls.map(url =>
    `<button type="button" class="btn preview-card" data-act="open-url" data-url="${esc(url)}" data-preview-url="${esc(url)}" title="${esc(url)}">${contents(url)}</button>`).join("")}</div>` : "";
}

/** Observes only cards near the reader, sharing in-flight and cached results across thread draws. */
export function createLinkPreviews(root: HTMLElement, fetch: (url: string) => Promise<LinkPreview | null>) {
  const cache = new Map<string, { until: number; result?: LinkPreview | null; promise: Promise<LinkPreview | null> }>();
  let active = false;
  const load = (card: HTMLElement): void => {
    const url = messageUrl(card.dataset.previewUrl);
    if (!url || card.dataset.previewLoaded) return;
    card.dataset.previewLoaded = "true";
    let entry = cache.get(url);
    if (!entry || entry.until <= Date.now()) {
      const promise = Promise.resolve().then(() => fetch(url)).catch(() => null);
      entry = { until: Date.now() + 86400_000, promise };
      cache.set(url, entry);
      const current = entry;
      void promise.then(result => { current.result = result; if (!result) current.until = Date.now() + 300_000; });
      // Keep the cache small even when searching through a long history.
      if (cache.size > 128) cache.delete(cache.keys().next().value!);
    }
    const paint = (result: LinkPreview | null): void => {
      if (active && root.contains(card)) { card.innerHTML = contents(url, result); card.dataset.previewState = result ? "ready" : "unavailable"; }
      else delete card.dataset.previewLoaded;
    };
    if (entry.result !== undefined) paint(entry.result);
    else void entry.promise.then(paint);
  };
  const observer = typeof IntersectionObserver === "function" ? new IntersectionObserver(entries => {
    for (const entry of entries) if (active && entry.isIntersecting) { observer!.unobserve(entry.target); load(entry.target as HTMLElement); }
  }, { root, rootMargin: "160px" }) : null;
  const fallback = (): void => {
    if (!active || observer) return;
    const view = root.getBoundingClientRect();
    for (const card of root.querySelectorAll<HTMLElement>("[data-preview-url]")) {
      const box = card.getBoundingClientRect();
      if (box.top <= view.bottom + 160 && box.bottom >= view.top - 160) load(card);
    }
  };
  root.addEventListener("scroll", fallback, { passive: true });
  return {
    sync(): void {
      active = true;
      observer?.disconnect();
      for (const card of root.querySelectorAll<HTMLElement>("[data-preview-url]:not([data-preview-loaded])")) observer?.observe(card);
      fallback();
    },
    pause(): void { active = false; observer?.disconnect(); },
  };
}
