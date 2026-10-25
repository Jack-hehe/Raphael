// Inline editable draft panel for `team_admin_batch` confirmations, and the settled one-line
// summary (`已建立 COO、PM、RD`) once the owner confirms.

import { avatarHTML } from "./avatar";
import { hasDetail, highlightHTML } from "./conversation";
import { t } from "./i18n";
import { INSTRUCTIONS_MAX, NAME_MAX, PERSONALITY_MAX, ROLE_MAX, faceHTML } from "./members";
import type {
  AgentQuestion,
  AgentTurn,
  AvatarPreset,
  GroupDraft,
  MemberDraft,
  MemberGroup,
} from "./types";
import {
  type Choice,
  actionRow,
  btn,
  closeMenu,
  esc,
  ico,
  iconButton,
  openMenu,
  openPopover,
  sel,
} from "./ui";

export function hasDrafts(question: AgentQuestion | null | undefined): question is AgentQuestion & { drafts: MemberDraft[] } {
  return Boolean(question && Array.isArray(question.drafts));
}

export function isSettledDraftTurn(turn: AgentTurn): boolean {
  if (turn.status !== "done" || !hasDetail(turn)) return false;
  const text = turn.text.trim();
  return (
    /^(?:已建立|已更新|已套用：|Created |Updated |Applied: )/.test(text) ||
    /(?:を作成しました|を更新しました)$/.test(text)
  );
}

export function settledDraftHTML(turn: AgentTurn, open: boolean, query = ""): string {
  const id = esc(turn.id);
  const summary = highlightHTML(turn.text.trim(), query);
  const lines = (turn.detail ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  return (
    `<div class="draft-done${open ? " is-open" : ""}" data-draft-done="${id}">` +
    btn(
      `<span class="draft-done-mark">${ico("check")}</span>` +
        `<span class="draft-done-text">${summary}</span>` +
        `<span class="steps-chev">${ico("right")}</span>`,
      "draft-done-sum",
      ` data-act="draft-done" aria-expanded="${open}" aria-controls="draft-done-${id}"`,
    ) +
    `<ul class="draft-done-list" id="draft-done-${id}"${open ? "" : " hidden"}>` +
    lines.map((line) => `<li class="draft-done-item">${highlightHTML(line, query)}</li>`).join("") +
    `</ul>` +
    `</div>`
  );
}

export function draftGroupChoices(
  groups: readonly MemberGroup[],
  draftGroups: readonly GroupDraft[],
  drafts: readonly MemberDraft[],
): Choice[] {
  const seen = new Set<string>();
  const choices: Choice[] = [{ value: "", label: t("members.ungrouped") }];
  for (const group of groups) {
    const name = group.name.trim();
    if (!name || seen.has(name)) continue;
    seen.add(name);
    choices.push({ value: name, label: name });
  }
  for (const group of draftGroups) {
    const name = group.name.trim();
    if (!name || seen.has(name)) continue;
    seen.add(name);
    choices.push({ value: name, label: name });
  }
  for (const draft of drafts) {
    const name = (draft.group ?? "").trim();
    if (!name || seen.has(name)) continue;
    seen.add(name);
    choices.push({ value: name, label: name });
  }
  return choices;
}

export function presetUrlOf(avatarPreset: string, presets: readonly AvatarPreset[]): string | null {
  const raw = (avatarPreset ?? "").trim();
  if (!raw) return null;
  const normalized = raw.startsWith("preset:") ? raw : `preset:${raw}`;
  const stem = raw.startsWith("preset:") ? raw.slice("preset:".length) : raw;
  const found = presets.find((p) => p.avatar === raw || p.avatar === normalized || p.name === stem);
  return found?.url ?? null;
}

export interface DraftPanelHost {
  groups(): readonly MemberGroup[];
  presets(): readonly AvatarPreset[];
  onConfirm(drafts: MemberDraft[]): void;
  onCancel(): void;
}

export interface DraftPanelController {
  readonly el: HTMLElement;
  sync(question: AgentQuestion | null, busy?: boolean): void;
  redraw(): void;
  drafts(): MemberDraft[];
}

export function createDraftPanel(host: DraftPanelHost): DraftPanelController {
  const el = document.createElement("div");
  el.className = "draft-panel";
  el.hidden = true;
  el.setAttribute("role", "group");

  let currentQuestionId: string | null = null;
  let currentDrafts: MemberDraft[] = [];
  let currentGroups: GroupDraft[] = [];
  let isBusy = false;
  const openMore = new Set<string>();

  function morePreview(draft: MemberDraft): string {
    const line = (draft.instructions || draft.personality || "").replace(/\s+/g, " ").trim();
    return line;
  }

  function primaryLabel(): string {
    const count = currentDrafts.length;
    const allCreate = count > 0 && currentDrafts.every((d) => d.member_id === undefined || d.member_id === null);
    return allCreate ? t("members.draftCreateCount", { p0: count }) : t("members.draftSaveCount", { p0: count });
  }

  function cardHTML(draft: MemberDraft, choices: Choice[], presets: readonly AvatarPreset[]): string {
    const id = esc(draft.id);
    const expanded = openMore.has(draft.id);
    const avatarUrl = presetUrlOf(draft.avatar_preset, presets);
    const face = faceHTML({ id: draft.member_id ?? -1, name: draft.name, avatar_url: avatarUrl }, 40);
    const peek = morePreview(draft);
    const removeLabel = t("members.draftRemove", { p0: draft.name.trim() || t("members.name") });
    return (
      `<article class="draft-card" data-draft-id="${id}">` +
      `<div class="draft-card-top">` +
      btn(
        face,
        "draft-av-btn",
        ` data-draft-act="avatar" aria-label="${esc(t("members.changeAvatar"))}" title="${esc(t("members.changeAvatar"))}" aria-haspopup="menu" aria-expanded="false"`,
      ) +
      `<div class="draft-card-ident">` +
      `<input type="text" class="mb-fin draft-name" data-draft-field="name" maxlength="${NAME_MAX}" value="${esc(draft.name)}" placeholder="${esc(t("members.name"))}" aria-label="${esc(t("members.name"))}" autocomplete="off" spellcheck="false"${isBusy ? " disabled" : ""}>` +
      `<input type="text" class="mb-fin draft-role" data-draft-field="role" maxlength="${ROLE_MAX}" value="${esc(draft.role)}" placeholder="${esc(t("members.role"))}" aria-label="${esc(t("members.role"))}" autocomplete="off" spellcheck="false"${isBusy ? " disabled" : ""}>` +
      `<span class="draft-group-wrap">${sel(choices, (draft.group ?? "").trim(), t("members.group"), ` data-draft-act="group"${isBusy ? " disabled" : ""}`)}</span>` +
      `</div>` +
      iconButton("x", removeLabel, ` data-draft-act="remove"${isBusy ? " disabled" : ""}`, "draft-remove") +
      `</div>` +
      `<div class="draft-card-blurb">` +
      `<input type="text" class="mb-fin draft-blurb" data-draft-field="blurb" maxlength="160" value="${esc(draft.blurb)}" placeholder="${esc(t("members.profile.blurb"))}" aria-label="${esc(t("members.profile.blurb"))}" autocomplete="off"${isBusy ? " disabled" : ""}>` +
      `</div>` +
      `<div class="draft-more${expanded ? " is-open" : ""}">` +
      btn(
        `<span class="steps-chev">${ico("right")}</span><span>${esc(t("members.draftMore"))}</span>` +
          (peek ? `<span class="draft-more-peek">${esc(peek)}</span>` : `<span class="draft-more-peek"></span>`),
        "draft-more-toggle",
        ` data-draft-act="more" aria-expanded="${expanded}" aria-controls="draft-more-${id}"`,
      ) +
      `<div class="draft-more-body" id="draft-more-${id}"${expanded ? "" : " hidden"}>` +
      `<label class="draft-field"><span>${esc(t("members.personality"))}</span>` +
      `<textarea class="mb-fin mb-ta draft-personality" data-draft-field="personality" maxlength="${PERSONALITY_MAX}" rows="2"${isBusy ? " disabled" : ""}>${esc(draft.personality)}</textarea></label>` +
      `<label class="draft-field"><span>${esc(t("members.instructions"))}</span>` +
      `<textarea class="mb-fin mb-ta draft-instructions" data-draft-field="instructions" maxlength="${INSTRUCTIONS_MAX}" rows="3"${isBusy ? " disabled" : ""}>${esc(draft.instructions)}</textarea></label>` +
      `</div>` +
      `</div>` +
      `</article>`
    );
  }

  function draw(): void {
    if (!currentQuestionId) return;
    const active = document.activeElement as HTMLElement | null;
    const focusedCardId = active && el.contains(active) ? active.closest<HTMLElement>("[data-draft-id]")?.dataset.draftId : undefined;
    const focusedField = active && el.contains(active) ? active.dataset.draftField : undefined;

    const choices = draftGroupChoices(host.groups(), currentGroups, currentDrafts);
    const presets = host.presets();
    const groupPills = currentGroups
      .filter((g) => g.name.trim() !== "")
      .map((g) => `<span class="draft-group-pill">${ico("folder")}<span>${esc(g.name.trim())}</span></span>`)
      .join("");

    el.setAttribute("aria-label", t("members.draftTitle"));
    el.innerHTML =
      `<div class="draft-head">` +
      `<span class="draft-face">${avatarHTML({ size: 28 })}</span>` +
      `<span class="draft-title">${esc(t("members.draftTitle"))}</span>` +
      groupPills +
      `</div>` +
      `<div class="draft-list">` +
      currentDrafts.map((draft) => cardHTML(draft, choices, presets)).join("") +
      `</div>` +
      actionRow({
        secondary: btn(esc(t("common.cancel")), "draft-cancel", ` data-draft-act="cancel"${isBusy ? " disabled" : ""}`),
        primary: {
          label: esc(primaryLabel()),
          attrs: ` data-draft-act="confirm"${isBusy ? " disabled" : ""}`,
        },
        cls: "draft-actions",
      });

    if (focusedCardId && focusedField) {
      const input = el.querySelector<HTMLInputElement | HTMLTextAreaElement>(
        `[data-draft-id="${CSS.escape(focusedCardId)}"] [data-draft-field="${CSS.escape(focusedField)}"]`,
      );
      input?.focus({ preventScroll: true });
    }
  }

  function openDraftAvatarMenu(anchor: HTMLElement, draft: MemberDraft): void {
    const presets = host.presets();
    const grid = presets.length
      ? `<div class="mb-presets" role="group" aria-label="${esc(t("settings.avatar"))}">` +
        presets
          .map(
            (preset, i) =>
              `<button type="button" role="menuitem" tabindex="-1" class="mb-preset" data-preset="${esc(preset.avatar)}" aria-label="${esc(t("members.preset", { p0: i + 1 }))}">` +
              `<img src="${esc(preset.url)}" alt="" draggable="false"></button>`,
          )
          .join("") +
        `</div><div class="menu-sep" role="separator"></div>`
      : "";
    const resetBtn = `<button type="button" role="menuitem" tabindex="-1" class="menu-action" data-preset=""><span class="mt">${esc(t("members.draftDefaultAvatar"))}</span></button>`;
    const pop = openPopover(anchor, grid + resetBtn, {
      className: "menu mb-avpop",
      role: "menu",
      label: t("settings.avatar"),
    });
    pop.addEventListener("click", (e) => {
      const target = (e.target as Element).closest<HTMLElement>("button[data-preset]");
      if (!target) return;
      closeMenu();
      draft.avatar_preset = target.dataset.preset ?? "";
      draw();
    });
    pop.querySelector<HTMLElement>("button")?.focus();
  }

  el.addEventListener("input", (e) => {
    const target = e.target as HTMLInputElement | HTMLTextAreaElement | null;
    if (!target || !target.dataset.draftField) return;
    const card = target.closest<HTMLElement>("[data-draft-id]");
    const draft = currentDrafts.find((d) => d.id === card?.dataset.draftId);
    if (!card || !draft) return;
    const field = target.dataset.draftField;
    if (field === "name") {
      draft.name = target.value;
      const removeBtn = card.querySelector<HTMLElement>('[data-draft-act="remove"]');
      if (removeBtn) {
        const label = t("members.draftRemove", { p0: draft.name.trim() || t("members.name") });
        removeBtn.setAttribute("aria-label", label);
        removeBtn.title = label;
      }
    } else if (field === "role") {
      draft.role = target.value;
    } else if (field === "blurb") {
      draft.blurb = target.value;
    } else if (field === "personality") {
      draft.personality = target.value;
      const peek = card.querySelector<HTMLElement>(".draft-more-peek");
      if (peek) peek.textContent = morePreview(draft);
    } else if (field === "instructions") {
      draft.instructions = target.value;
      const peek = card.querySelector<HTMLElement>(".draft-more-peek");
      if (peek) peek.textContent = morePreview(draft);
    }
  });

  el.addEventListener("click", (e) => {
    const act = (e.target as Element).closest<HTMLElement>("[data-draft-act]");
    if (!act || (act as HTMLButtonElement).disabled || isBusy) return;
    const action = act.dataset.draftAct;
    if (action === "confirm") {
      host.onConfirm(currentDrafts.map((d) => ({ ...d })));
      return;
    }
    if (action === "cancel") {
      host.onCancel();
      return;
    }
    const card = act.closest<HTMLElement>("[data-draft-id]");
    const draft = currentDrafts.find((d) => d.id === card?.dataset.draftId);
    if (!card || !draft) return;
    if (action === "remove") {
      currentDrafts = currentDrafts.filter((d) => d.id !== draft.id);
      openMore.delete(draft.id);
      draw();
    } else if (action === "group") {
      const choices = draftGroupChoices(host.groups(), currentGroups, currentDrafts);
      openMenu(act, choices, (draft.group ?? "").trim(), (value) => {
        draft.group = value;
        draw();
      });
    } else if (action === "avatar") {
      openDraftAvatarMenu(act, draft);
    } else if (action === "more") {
      const body = card.querySelector<HTMLElement>(".draft-more-body");
      const wrap = card.querySelector<HTMLElement>(".draft-more");
      if (!body || !wrap) return;
      const next = Boolean(body.hidden);
      body.hidden = !next;
      wrap.classList.toggle("is-open", next);
      act.setAttribute("aria-expanded", String(next));
      if (next) openMore.add(draft.id);
      else openMore.delete(draft.id);
    }
  });

  return {
    el,
    sync(question, busy = false) {
      if (!hasDrafts(question)) {
        currentQuestionId = null;
        currentDrafts = [];
        currentGroups = [];
        openMore.clear();
        isBusy = false;
        el.hidden = true;
        delete el.dataset.id;
        return;
      }
      const changedQuestion = question.id !== currentQuestionId;
      const changedBusy = busy !== isBusy;
      isBusy = busy;
      if (changedQuestion) {
        currentQuestionId = question.id;
        currentDrafts = question.drafts.map((d) => ({ ...d }));
        currentGroups = (question.groups ?? []).map((g) => ({ ...g }));
        openMore.clear();
        el.dataset.id = question.id;
      }
      el.hidden = false;
      if (changedQuestion || changedBusy) draw();
    },
    redraw() {
      if (currentQuestionId && !el.hidden) draw();
    },
    drafts: () => currentDrafts.map((d) => ({ ...d })),
  };
}
