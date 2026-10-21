// The local tab of the speech-to-text picker (model-picker.ts): the catalogue of the speech models
// that run on this computer (`models.local`), none downloaded until the user chooses it. Each row
// has the model's name, the languages it knows, its size, a line on what it is for and its state:
// 未下載, 下載中 42%, 已下載 or 使用中. Choosing a model that is not downloaded asks first, in the
// row, with its size; a downloaded one that is not in use can be deleted, also after asking.
//
// This file only draws and counts. The picker keeps the open tab and the question being asked,
// and the settings page does the downloading and holds the progress of it.

import { t } from "./i18n";
import type { LocalModel } from "./types";
import { CHECK, btn, esc, ico } from "./ui";

/** What the local tab is drawn from. */
export interface LocalView {
  /** The catalogue (`models.local`), or why there is none. */
  models: LocalModel[] | "loading" | "failed";
  /** The model of the speech-to-text in use; null while the cloud is in use. */
  active: string | null;
  /** The download under way: bytes done of bytes in all (0 while the size is not known). */
  download: { name: string; done: number; total: number } | null;
}

/** A question the row of `name` asks before it does anything. */
export interface LocalConfirm {
  kind: "download" | "delete";
  name: string;
}

export type LocalState =
  | { kind: "absent" }
  | { kind: "downloading"; percent: number }
  | { kind: "installing" }
  | { kind: "ready" }
  | { kind: "active" };

const MB = 1_000_000;
const GB = 1_000_000_000;

/** A download's size as it is announced: `163 MB`, `1.8 GB`. */
export function formatSize(bytes: number): string {
  if (bytes >= GB) return `${(bytes / GB).toFixed(1)} GB`;
  return `${Math.round(bytes / MB)} MB`;
}

const LANGUAGES: Record<string, () => string> = {
  zh: () => t("local.langZh"),
  en: () => t("local.langEn"),
  ja: () => t("local.langJa"),
  ko: () => t("local.langKo"),
  yue: () => t("local.langYue"),
  multi: () => t("local.langMulti"),
};

/** The languages of a model, named in the language of the window. */
export const languagesText = (codes: readonly string[]): string => codes.map((code) => LANGUAGES[code]?.() ?? code).join(t("local.listSeparator"));

const NOTES: Record<string, () => string> = {
  dictionary: () => t("local.noteDictionary"),
  fast: () => t("local.noteFast"),
  taiwan: () => t("local.noteTaiwan"),
  mandarin: () => t("local.noteMandarin"),
  chinese: () => t("local.noteChinese"),
  multilingual: () => t("local.noteMultilingual"),
  balanced: () => t("local.noteBalanced"),
  small: () => t("local.noteSmall"),
};

const percentOf = (done: number, total: number): number => (total > 0 ? Math.min(100, Math.floor((done / total) * 100)) : 0);

/** Where a model stands: in use, being fetched, installed after the last byte, on disk, or not there. */
export function localState(model: LocalModel, view: LocalView): LocalState {
  const { download } = view;
  if (download?.name === model.name) {
    return download.total > 0 && download.done >= download.total ? { kind: "installing" } : { kind: "downloading", percent: percentOf(download.done, download.total) };
  }
  if (model.name === view.active) return { kind: "active" };
  return model.ready ? { kind: "ready" } : { kind: "absent" };
}

/** The state in words. */
export function stateText(state: LocalState): string {
  switch (state.kind) {
    case "absent":
      return t("local.stateAbsent");
    case "downloading":
      return t("local.stateDownloading", { p0: state.percent });
    case "installing":
      return t("local.stateInstalling");
    case "ready":
      return t("local.stateReady");
    case "active":
      return t("local.stateActive");
  }
}

/** What the picker's button says while a local model is in use: 「本機 · SenseVoice」; null while the cloud is. */
export function faceTitle(view: LocalView): string | null {
  if (view.active === null) return null;
  const model = view.models === "loading" || view.models === "failed" ? undefined : view.models.find((m) => m.name === view.active);
  return `${t("picker.tabLocal")} · ${model?.title ?? view.active}`;
}

/** The two tabs over the picker, 雲端 and 本機: real buttons, the open one selected and the only Tab stop. */
export function tabsHTML(open: "cloud" | "local"): string {
  const tab = (which: "cloud" | "local", label: string): string =>
    `<button type="button" class="mp-tab" role="tab" id="mp-tab-${which}" data-tab="${which}" tabindex="${open === which ? 0 : -1}" aria-selected="${open === which}" aria-controls="mp-pane-${which}">${esc(label)}</button>`;
  return `<div class="mp-tabs" role="tablist" aria-label="${esc(t("picker.tabs"))}">${tab("cloud", t("picker.tabCloud"))}${tab("local", t("picker.tabLocal"))}</div>`;
}

function rowHTML(model: LocalModel, view: LocalView, confirm: LocalConfirm | null): string {
  const state = localState(model, view);
  const busy = view.download !== null;
  const size = formatSize(model.size);
  const open = `<div class="lm-row" data-name="${esc(model.name)}">`;
  if (confirm?.name === model.name) {
    const text = confirm.kind === "download" ? t("local.confirmDownload", { p0: model.title, p1: size }) : t("local.confirmDelete", { p0: model.title, p1: size });
    return (
      `${open}<div class="lm-confirm" role="group" aria-label="${esc(text)}"><span class="lm-ask">${esc(text)}</span>` +
      btn(esc(confirm.kind === "download" ? t("local.download") : t("local.delete")), "pri sm", ` data-act="confirm" data-name="${esc(model.name)}"`) +
      btn(esc(t("local.cancel")), "sm", ` data-act="dismiss" data-name="${esc(model.name)}"`) +
      `</div></div>`
    );
  }
  const languages = languagesText(model.languages);
  const note = NOTES[model.note]?.() ?? "";
  const sub = [languages, size, note].filter(Boolean).join(" · ");
  const label = [model.title, languages, size, note, stateText(state)].filter(Boolean).join(", ");
  const fetching = state.kind === "downloading" || state.kind === "installing";
  const bar = fetching
    ? `<span class="lm-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${state.kind === "installing" ? 100 : state.percent}" aria-label="${esc(model.title)}"><i style="width:${state.kind === "installing" ? 100 : state.percent}%"></i></span>`
    : "";
  const main =
    `<button type="button" class="lm-main" data-act="use" data-name="${esc(model.name)}"${state.kind === "active" ? ' aria-current="true"' : ""}${busy ? " disabled" : ""} aria-label="${esc(label)}">` +
    `<span class="ck">${state.kind === "active" ? CHECK : ""}</span>` +
    `<span class="mp-main"><span class="mp-line"><span class="mp-name">${esc(model.title)}</span><span class="lm-state num" data-state>${esc(stateText(state))}</span></span>` +
    `<span class="mp-sub">${esc(sub)}</span>${bar}</span></button>`;
  let action = "";
  if (fetching) {
    action = btn(esc(t("local.cancel")), "sm", ` data-act="cancel" aria-label="${esc(t("local.cancelDownload", { p0: model.title }))}"`);
  } else if (model.ready && state.kind !== "active" && !busy) {
    const text = t("local.deleteLabel", { p0: model.title });
    action = `<button type="button" class="lm-del" data-act="delete" data-name="${esc(model.name)}" aria-label="${esc(text)}" title="${esc(text)}">${ico("trash")}</button>`;
  }
  return `${open}${main}${action}</div>`;
}

/** The rows of the local tab, with the question being asked in one of them. */
export function localListHTML(view: LocalView, confirm: LocalConfirm | null): string {
  if (view.models === "loading") return `<p class="mp-empty" role="status">${esc(t("common.loading"))}</p>`;
  if (view.models === "failed") return `<p class="mp-empty" role="status">${esc(t("local.unavailable"))}</p>`;
  return view.models.map((model) => rowHTML(model, view, confirm)).join("");
}
