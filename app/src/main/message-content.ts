// A deliberately small Markdown renderer. Only generated tags enter HTML; message text always
// passes through highlightHTML/esc. Images and raw HTML are never interpreted or fetched.
import { highlightHTML } from "./conversation";
import { t } from "./i18n";
import { esc, iconBtn } from "./ui";

export function messageUrl(raw: unknown): string | null {
  if (typeof raw !== "string" || raw.length > 8192 || /[\u0000-\u0020\u007f]/.test(raw)) return null;
  try {
    const url = new URL(raw);
    return /^(https?:)$/.test(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

export function shortUrl(url: string): string {
  const parsed = new URL(url);
  const label = parsed.host + (parsed.pathname === "/" ? "" : parsed.pathname);
  return label.length > 32 ? label.slice(0, 31) + "…" : label;
}

function bareUrl(text: string): string {
  let raw = /^https?:\/\/[^\s<>"'`《》「」『』【】（）。，、！？；：…]+/i.exec(text)?.[0] ?? "";
  raw = raw.replace(/[.,;:!?*_]+$/, "");
  for (const [open, close] of [["(", ")"], ["[", "]"], ["{", "}"]]) {
    while (raw.endsWith(close) && raw.split(close).length > raw.split(open).length) raw = raw.slice(0, -1);
  }
  return raw;
}

function inline(text: string, query: string, markdown: boolean, urls: Set<string>, depth = 0): string {
  let out = "", at = 0, plain = "";
  const flush = (): void => { out += highlightHTML(plain, query); plain = ""; };
  const link = (raw: string, label?: string): string | null => {
    const url = messageUrl(raw);
    if (!url) return null;
    urls.add(url);
    return `<a class="message-link" href="${esc(url)}" data-act="open-url" data-url="${esc(url)}" rel="noreferrer noopener">${highlightHTML(label ?? shortUrl(url), query)}</a>`;
  };
  while (at < text.length) {
    if (markdown && text[at] === "\\" && at + 1 < text.length) { plain += text[at + 1]; at += 2; continue; }
    if (markdown && text[at] === "`") {
      const end = text.indexOf("`", at + 1);
      if (end > at + 1) { flush(); out += `<code>${esc(text.slice(at + 1, end))}</code>`; at = end + 1; continue; }
    }
    if (markdown && text[at] === "[") {
      const middle = text.indexOf("](", at + 1);
      if (middle !== -1) {
        let end = middle + 2, level = 1;
        for (; end < text.length && level; end++) { if (text[end] === "(") level++; if (text[end] === ")") level--; }
        if (!level) {
          const html = link(text.slice(middle + 2, end - 1), text.slice(at + 1, middle));
          if (html) { flush(); out += html; at = end; continue; }
        }
      }
    }
    const raw = /^https?:\/\//i.test(text.slice(at, at + 8)) ? bareUrl(text.slice(at)) : "";
    if (raw) {
      const html = link(raw);
      if (html) { flush(); out += html; at += raw.length; continue; }
    }
    if (markdown && depth < 8 && /[*_]/.test(text[at])) {
      const marker = text[at + 1] === text[at] ? text.slice(at, at + 2) : text[at];
      const end = text.indexOf(marker, at + marker.length);
      if (end > at + marker.length) {
        flush(); const tag = marker.length === 2 ? "strong" : "em";
        out += `<${tag}>${inline(text.slice(at + marker.length, end), query, true, urls, depth + 1)}</${tag}>`;
        at = end + marker.length; continue;
      }
    }
    plain += text[at++];
  }
  flush(); return out;
}

function render(text: string, query: string, markdown: boolean, urls: Set<string>): string {
  if (!markdown) return inline(text, query, false, urls);
  const wrapped = /^《([^。\n]{1,20}。)([\s\S]*)》$/.exec(text);
  if (wrapped) {
    let body = render(wrapped[2], query, true, urls);
    const prefix = highlightHTML(`《${wrapped[1]}`, query);
    body = body.startsWith("<p>") ? `<p>${prefix}${body.slice(3)}` : `<p>${prefix}</p>${body}`;
    return body.endsWith("</p>") ? `${body.slice(0, -4)}》</p>` : `${body}<p>》</p>`;
  }
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  let out = "", list = "", paragraph: string[] = [];
  const flush = (): void => { if (paragraph.length) out += `<p>${paragraph.join("<br>")}</p>`; paragraph = []; };
  const closeList = (): void => { if (list) out += `</${list}>`; list = ""; };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const fence = /^\s*(`{3,}|~{3,})[^`]*$/.exec(line);
    if (fence) {
      flush(); closeList(); const code: string[] = [];
      const ending = new RegExp(`^\\s*${fence[1][0]}{${fence[1].length},}\\s*$`);
      while (++i < lines.length && !ending.test(lines[i])) code.push(lines[i]);
      out += `<div class="message-code"><pre><code>${esc(code.join("\n"))}</code></pre>${iconBtn("copy", t("chat.copyCode"), ' data-act="copy-code"')}</div>`;
      continue;
    }
    const item = /^\s*(?:([-+*])|([0-9]+)[.)])\s+(.+)$/.exec(line);
    if (item) {
      flush(); const tag = item[1] ? "ul" : "ol";
      if (tag !== list) { closeList(); list = tag; out += `<${tag}${item[2] ? ` start="${Number(item[2])}"` : ""}>`; }
      out += `<li>${inline(item[3], query, true, urls)}</li>`;
    } else {
      closeList();
      if (!line.trim()) flush();
      else paragraph.push(inline(line, query, true, urls));
    }
  }
  flush(); closeList(); return out;
}

export const messageHTML = (text: string, query = "", markdown = false): string => render(text, query, markdown, new Set());
export function messageUrls(text: string): string[] {
  const urls = new Set<string>(); render(text, "", true, urls); return [...urls];
}

/** Copy rendered Markdown as text, preserving full addresses even when their labels are short. */
export function messagePlainText(text: string, markdown = false): string {
  if (!markdown) return text;
  const el = document.createElement("div"); el.innerHTML = messageHTML(text, "", true);
  el.querySelectorAll("button").forEach(button => button.remove());
  for (const link of el.querySelectorAll<HTMLAnchorElement>("a")) {
    const url = link.dataset.url!;
    link.textContent = link.textContent === shortUrl(url) ? url : `${link.textContent} (${url})`;
  }
  const read = (node: Node): string => {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent ?? "";
    if (!(node instanceof Element)) return "";
    if (node.tagName === "BR") return "\n";
    const content = [...node.childNodes].map(read).join("");
    if (node.tagName === "LI") {
      const parent = node.parentElement;
      const number = parent?.tagName === "OL" ? Number(parent.getAttribute("start") || 1) + [...parent.children].indexOf(node) : null;
      return `${number === null ? "-" : `${number}.`} ${content}\n`;
    }
    return /^(P|LI|PRE)$/.test(node.tagName) ? content + "\n" : content;
  };
  return read(el).trimEnd();
}
