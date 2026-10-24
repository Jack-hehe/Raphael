/** Preview only server metadata; secret values never return from the core. */
import * as api from "./api";
import { t } from "./i18n";
import { describeError } from "./format";
import { openDialog } from "./member-dialogs";
import type { McpPreview } from "./types";
import { btn, esc } from "./ui";

export function openMcpImport(discover: boolean, changed: () => void): void {
  let preview: McpPreview | null = null, busy = false;
  const dialog = openDialog({
    title: t(discover ? "mcp.fromApps" : "mcp.paste"),
    body: (discover ? `<p>${esc(t("mcp.importHint"))}</p>` : `<label class="mcp-import-label">${esc(t("mcp.json"))}<textarea class="field" data-mcp-json rows="7" spellcheck="false" placeholder='{"mcpServers": {}}'></textarea></label>`) + `<div data-mcp-preview role="status"></div>`,
    actions: { secondary: btn(t("mcp.preview"), "", ' data-dlg="preview"'), primary: { label: t("mcp.import"), attrs: ' data-dlg="import" disabled' } },
    onAct(act) { if (act === "preview") void read(); else if (act === "import") void save(); },
    canClose: () => !busy,
    onClose() { const field = dialog.el.querySelector<HTMLTextAreaElement>("[data-mcp-json]"); if (field) field.value = ""; },
  });
  const status = dialog.el.querySelector<HTMLElement>("[data-mcp-preview]")!;
  const add = dialog.el.querySelector<HTMLButtonElement>('[data-dlg="import"]')!;
  status.addEventListener("change", () => { add.disabled = busy || !status.querySelector("[data-mcp-candidate]:checked"); });
  async function read(): Promise<void> {
    if (busy) return;
    busy = true; add.disabled = true;
    status.textContent = t("usage.loading");
    try {
      preview = discover ? await api.integrationsDiscover() : await api.integrationsPreview(dialog.el.querySelector<HTMLTextAreaElement>("[data-mcp-json]")!.value);
      status.innerHTML = preview.candidates.length ? `<div class="mcp-candidates">` + preview.candidates.map(c =>
        `<label><input type="checkbox" data-mcp-candidate value="${esc(c.id)}" checked><span><strong>${esc(c.name)}</strong><small>${esc(c.source)}</small><code>${esc(c.transport === "http" ? c.url : [c.command, ...c.args].join(" "))}</code></span></label>`).join("") + `</div>` : `<p>${esc(t("mcp.nothing"))}</p>`;
      if (preview.rejected) status.innerHTML += `<p>${esc(t("mcp.skipped"))}: ${preview.rejected}</p>`;
      add.disabled = !preview.candidates.length;
    } catch (error) { status.textContent = describeError(error); }
    busy = false;
  }
  async function save(): Promise<void> {
    if (busy || !preview) return;
    const ids = Array.from(dialog.el.querySelectorAll<HTMLInputElement>("[data-mcp-candidate]:checked")).map(input => input.value);
    if (!ids.length) return;
    busy = true; add.disabled = true;
    try { await api.integrationsImport(ids); dialog.close(); changed(); }
    catch (error) { status.textContent = describeError(error); add.disabled = false; }
    busy = false;
  }
  if (discover) void read();
}
