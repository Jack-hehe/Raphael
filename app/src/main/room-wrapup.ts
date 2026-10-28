import { t } from "./i18n";
import { messageHTML } from "./message-content";
import { esc } from "./ui";

/** Interpret model headings as card sections while keeping all content escaped. */
export function roomWrapupHTML(text: string, names: string[]): string {
  const labels = [t("rooms.wrapupConclusion"), t("rooms.wrapupDecision"), t("rooms.wrapupNext")];
  const aliases = [
    /^(?:結論|結論と合意|conclusion|conclusions|summary)$/i,
    /^(?:待你決定|待決定|未決事項|決めてほしいこと|owner decision|decision|decisions|open questions?)$/i,
    /^(?:下一步|次のステップ|next steps?|actions?)$/i,
  ];
  const sections: string[][] = [[], [], []];
  let section = 0, fenced = false;
  for (const line of text.replace(/\r\n?/g, "\n").split("\n")) {
    if (/^\s*(`{3,}|~{3,})/.test(line)) fenced = !fenced;
    const heading = line.trim().replace(/^#{1,6}\s+/, "").replace(/^\*\*(.*?)\*\*$/, "$1").replace(/[:：]\s*$/, "");
    const index = fenced ? -1 : aliases.findIndex(pattern => pattern.test(heading));
    if (index >= 0) section = index;
    else sections[section].push(line);
  }
  return sections.map((lines, index) => {
    if (!lines.join("\n").trim()) return "";
    const body = document.createElement("div");
    body.innerHTML = messageHTML(lines.join("\n"), "", true);
    if (index === 2) for (const row of body.querySelectorAll("li, p")) {
      for (const node of [...row.childNodes]) {
        if (node.nodeType === Node.TEXT_NODE) {
          const value = node.textContent ?? "";
          const name = [...names].sort((a, b) => b.length - a.length).find(name => value.startsWith(name + "：") || value.startsWith(name + ":"));
          if (!name) continue;
          const owner = document.createElement("strong");
          owner.className = "au-room-wrapup-owner"; owner.textContent = name;
          node.replaceWith(owner, document.createTextNode(value.slice(name.length)));
        } else if (node instanceof HTMLElement && node.tagName === "STRONG" && names.includes(node.textContent?.replace(/[:：]$/, "") ?? "")) {
          node.classList.add("au-room-wrapup-owner");
        }
      }
    }
    return `<section class="au-room-wrapup-section" data-wrapup-section="${index}"><h3>${esc(labels[index])}</h3>${body.innerHTML}</section>`;
  }).join("");
}
