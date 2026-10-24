/** Shared owner profile and appearance controls. */
import * as api from "./api";
import { t } from "./i18n";
import type { BrainSettings } from "./types";
import { esc, toaster } from "./ui";
import { describeError } from "./format";
import "./owner-settings.css";

export const SKINS = ["default", "paper", "graphite", "lagoon", "dusk", "midnight", "meadow", "ink"] as const;
export type Skin = typeof SKINS[number];
export function skinOf(value: unknown): Skin { return SKINS.includes(value as Skin) ? value as Skin : "default"; }
const darkSkins = new Set<Skin>(["graphite", "dusk", "midnight"]);
export function applySkin(value: unknown, appearance: "system" | "light" | "dark", systemDark: boolean): void {
  const skin = skinOf(value);
  document.documentElement.dataset.skin = skin;
  document.documentElement.dataset.theme = skin === "default" ? (appearance === "system" ? systemDark ? "dark" : "light" : appearance) : darkSkins.has(skin) ? "dark" : "light";
}
export function ownerProfileHTML(brain: BrainSettings | null): string {
  return `<section class="sgroup owner-profile"><h2>${esc(t("owner.profile"))}</h2><div class="slist">` +
    `<label>${esc(t("owner.name"))}<input class="field" data-owner="owner_name" maxlength="100" value="${esc(brain?.owner_name ?? "")}" ${brain ? "" : "disabled"}></label>` +
    `<label>${esc(t("owner.about"))}<textarea class="field" rows="4" data-owner="owner_about" maxlength="4000" ${brain ? "" : "disabled"}>${esc(brain?.owner_about ?? "")}</textarea></label></div></section>`;
}
export function skinsHTML(brain: BrainSettings | null): string {
  const selected = skinOf(brain?.skin);
  return `<section class="sgroup"><h2>${esc(t("skin.styles"))}</h2><div class="skin-grid">` + SKINS.map(skin =>
    `<button type="button" class="btn skin-card" data-skin-choice="${skin}" aria-pressed="${skin === selected}" ${brain ? "" : "disabled"}>` +
    `<span class="skin-preview" data-preview="${skin}" aria-hidden="true"><i class="skin-sidebar"></i><i class="skin-bubble"></i><i class="skin-reply"></i><i class="skin-composer"></i></span>` +
    `<strong>${esc(t(`skin.${skin}`))}</strong><small>${esc(t(`skin.${skin}Desc`))}</small></button>`).join("") + `</div></section>`;
}

export function createOwnerControls(saved: (settings: BrainSettings) => void) {
  let writes = Promise.resolve();
  const set = (key: "owner_name" | "owner_about" | "skin", value: string): void => {
    writes = writes.then(async () => { saved((await api.settingsSet(key, value)).settings); }).catch(error => toaster.info(describeError(error)));
  };
  return {
    click(target: Element): boolean {
      const skin = target.closest<HTMLElement>("[data-skin-choice]");
      if (skin) { set("skin", skinOf(skin.dataset.skinChoice)); return true; }
      return false;
    },
    async change(target: Element): Promise<void> {
      if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) {
        if (target.dataset.owner === "owner_name" || target.dataset.owner === "owner_about") set(target.dataset.owner, target.value);
      }
    },
  };
}
