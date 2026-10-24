/** Shared owner settings. The portrait is presentation data and never enters a model prompt. */
import * as api from "./api";
import { t } from "./i18n";
import type { BrainSettings } from "./types";
import { btn, esc, ico, toaster } from "./ui";
import { describeError } from "./format";
import "./owner-settings.css";

export const SKINS = ["default", "paper", "graphite", "lagoon", "dusk", "midnight", "meadow", "ink", "catppuccin"] as const;
export type Skin = typeof SKINS[number];
export function skinOf(value: unknown): Skin { return SKINS.includes(value as Skin) ? value as Skin : "default"; }
export function applySkin(value: unknown, appearance: "system" | "light" | "dark", systemDark: boolean): void {
  const skin = skinOf(value);
  document.documentElement.dataset.skin = skin;
  document.documentElement.dataset.theme = appearance === "system" ? systemDark ? "dark" : "light" : appearance;
}
function portraitHTML(photo = ""): string {
  return /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/]+=*$/.test(photo) ? `<img class="owner-photo" src="${esc(photo)}" alt="">` : ico("person");
}
export function ownerButtonHTML(photo = ""): string {
  return `<button type="button" class="nav nav-bottom" data-owner-menu aria-haspopup="menu" aria-label="${esc(t("nav.you"))}">` +
    portraitHTML(photo) +
    `<span>${esc(t("nav.you"))}</span></button>`;
}
export function ownerProfileHTML(brain: BrainSettings | null): string {
  return `<section class="sgroup owner-profile"><h2>${esc(t("owner.profile"))}</h2><div class="slist">` +
    `<label>${esc(t("owner.name"))}<input class="field" data-owner="owner_name" maxlength="100" value="${esc(brain?.owner_name ?? "")}" ${brain ? "" : "disabled"}></label>` +
    `<label>${esc(t("owner.about"))}<textarea class="field" rows="4" data-owner="owner_about" maxlength="4000" ${brain ? "" : "disabled"}>${esc(brain?.owner_about ?? "")}</textarea></label>` +
    `<div class="owner-actions"><span class="owner-photo-preview" aria-hidden="true">${portraitHTML(brain?.owner_photo)}</span>${btn(t("owner.photo"), "", ' data-owner-pick' + (brain ? "" : " disabled"))}` +
    (brain?.owner_photo ? btn(t("owner.removePhoto"), "danger", ' data-owner-remove') : "") +
    `<input type="file" data-owner-file accept="image/png,image/jpeg,image/webp" hidden></div></div></section>`;
}
export function skinsHTML(brain: BrainSettings | null): string {
  const selected = skinOf(brain?.skin);
  return `<section class="sgroup"><h2>${esc(t("skin.styles"))}</h2><div class="skin-grid">` + SKINS.map(skin =>
    `<button type="button" class="btn skin-card" data-skin-choice="${skin}" aria-pressed="${skin === selected}" ${brain ? "" : "disabled"}>` +
    `<span class="skin-preview" aria-hidden="true">${["light", "dark"].map(mode => `<span class="skin-sample" data-preview="${skin}" data-mode="${mode}"><i class="skin-sidebar"></i><i class="skin-bubble"></i><i class="skin-reply"></i><i class="skin-composer"></i></span>`).join("")}</span>` +
    `<strong>${esc(t(`skin.${skin}`))}</strong><small>${esc(t(`skin.${skin}Desc`))}</small></button>`).join("") + `</div></section>`;
}

export function createOwnerControls(root: HTMLElement, saved: (settings: BrainSettings) => void) {
  let writes = Promise.resolve();
  const set = (key: "owner_name" | "owner_about" | "owner_photo" | "skin", value: string): void => {
    writes = writes.then(async () => { saved((await api.settingsSet(key, value)).settings); }).catch(error => toaster.info(describeError(error)));
  };
  return {
    click(target: Element): boolean {
      const skin = target.closest<HTMLElement>("[data-skin-choice]");
      if (skin) { set("skin", skinOf(skin.dataset.skinChoice)); return true; }
      if (target.closest("[data-owner-pick]")) { root.querySelector<HTMLInputElement>("[data-owner-file]")?.click(); return true; }
      if (target.closest("[data-owner-remove]")) { set("owner_photo", ""); return true; }
      return false;
    },
    async change(target: Element): Promise<void> {
      if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) {
        if (target.dataset.owner === "owner_name" || target.dataset.owner === "owner_about") set(target.dataset.owner, target.value);
      }
      if (!(target instanceof HTMLInputElement) || !target.matches("[data-owner-file]")) return;
      const file = target.files?.[0];
      if (!file) return;
      try {
        if (!["image/png", "image/jpeg", "image/webp"].includes(file.type) || file.size > 10_000_000) throw new Error(t("owner.badPhoto"));
        const bitmap = await createImageBitmap(file);
        const canvas = document.createElement("canvas");
        canvas.width = canvas.height = 160;
        const size = Math.min(bitmap.width, bitmap.height);
        canvas.getContext("2d")!.drawImage(bitmap, (bitmap.width - size) / 2, (bitmap.height - size) / 2, size, size, 0, 0, 160, 160);
        bitmap.close();
        set("owner_photo", canvas.toDataURL("image/webp", 0.85));
      } catch { toaster.info(t("owner.badPhoto")); }
      target.value = "";
    },
  };
}
