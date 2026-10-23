// Ordered failover choices share the primary picker's catalog and provider references.
import { t } from "./i18n";
import { modelPickerHTML, type ModelPickerLook } from "./model-picker";
import type { BrainSettings, ModelPurpose } from "./types";
import { btn, esc, iconButton } from "./ui";
import "./model-fallbacks.css";

export const FALLBACK_PURPOSES: readonly ModelPurpose[] = ["agent", "cleanup", "router", "vision", "pdf", "stt", "memory"];
export const fallbackKey = (purpose: ModelPurpose): `${ModelPurpose}_fallbacks` => `${purpose}_fallbacks`;
export const fallbackRefs = (brain: BrainSettings | null, purpose: ModelPurpose): string[] => (brain?.[fallbackKey(purpose)] ?? []).slice(0, 3);

export function fallbackLook(base: ModelPickerLook, purpose: ModelPurpose, index: number, current: string, busy = false): ModelPickerLook {
  return { ...base, current, key: `fallback-${purpose}-${index}`, label: t("fallbacks.pick", { p0: index + 1 }),
    local: undefined, showPrice: false, disabled: base.disabled || busy,
    lead: current ? undefined : { id: "", name: t("fallbacks.choose") } };
}

export function fallbacksHTML(brain: BrainSettings | null, purpose: ModelPurpose, look: ModelPickerLook, draft = false, busy = false): string {
  const refs = fallbackRefs(brain, purpose);
  if (draft && refs.length < 3) refs.push("");
  const disabled = busy || brain === null;
  return `<div class="mf" data-fallback-purpose="${purpose}" role="group" aria-label="${esc(t("fallbacks.title"))}">` +
    `<h3>${esc(t("fallbacks.title"))}</h3><p class="note">${esc(t("fallbacks.about"))}</p>` +
    refs.map((ref, index) => {
      const attrs = (action: string, off = false) => ` data-fallback-act="${action}" data-k="fallback-${purpose}-${index}-${action}"${disabled || off ? " disabled" : ""}`;
      return `<div class="mf-row" data-fallback-index="${index}">${modelPickerHTML(fallbackLook(look, purpose, index, ref, busy))}` +
        iconButton("up", t("fallbacks.up", { p0: index + 1 }), attrs("up", index === 0 || !ref || !refs[index - 1])) +
        iconButton("down", t("fallbacks.down", { p0: index + 1 }), attrs("down", index === refs.length - 1 || !ref || !refs[index + 1])) +
        iconButton("trash", t("fallbacks.remove", { p0: index + 1 }), attrs("remove")) + `</div>`;
    }).join("") + btn(esc(t("fallbacks.add")), "", ` data-fallback-act="add" data-k="fallback-${purpose}-add"${disabled || refs.length >= 3 || draft ? " disabled" : ""}`) + `</div>`;
}
