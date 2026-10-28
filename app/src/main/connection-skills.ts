// The private skills of the owner's members, in the connections tab beside the apps: a row for each, and the page of one
// (its name, when it is used, its instructions as Markdown to edit, and 「刪除技能」). A skill is a Markdown document a member
// is told of and reads when its description fits the request; it is on or off, and deleting it stops it at once.
// Nothing here is a second skills page: the member's own tab (member-profile-resources.ts) still imports and lists the same skills.

import * as api from "./api";
import { describeError } from "./format";
import { t } from "./i18n";
import { faceHTML } from "./members";
import type { MemberSkill } from "./types";
import { actionRow, btn, esc, ico, st, tg } from "./ui";

/** A skill with the member whose it is. */
export interface SkillItem {
  member: { id: number; name: string; avatar_url?: string };
  skill: MemberSkill;
}

/** The page of one skill that is open: its document as saved and as it is being edited. */
export interface SkillPage {
  memberId: number;
  id: number;
  /** The saved document; null until it is read. */
  saved: string | null;
  draft: string;
  state: "loading" | "ready" | "failed";
  busy: boolean;
  /** What the core refused, already in the words of the window. */
  error: string | null;
}

export const skillPage = (memberId: number, id: number): SkillPage =>
  ({ memberId, id, saved: null, draft: "", state: "loading", busy: false, error: null });

export const sameSkill = (item: SkillItem, memberId: number, id: number): boolean => item.member.id === memberId && item.skill.id === id;

/** The skills of every member (Raphael has none), in the order of the members, then of the skills. */
export async function loadSkillItems(): Promise<SkillItem[]> {
  const { members } = await api.membersList();
  const lists = await Promise.all(members.filter((member) => member.id !== 0).map(async (member) =>
    (await api.membersSkillsList(member.id)).skills.map((skill): SkillItem => ({ member: { id: member.id, name: member.name, avatar_url: member.avatar_url }, skill }))));
  return lists.flat();
}

/** A row of the list, drawn like an app's: the member's face, the skill's name, who has it and when it is used, a switch and a chevron. */
export function skillRowHTML(item: SkillItem): string {
  const { member, skill } = item;
  const ids = ` data-member="${member.id}" data-id="${skill.id}"`;
  const key = `${member.id}:${skill.id}`;
  return (
    `<div class="cs-item cs-skill" data-conn="skill-open"${ids}>` +
    btn(
      `<span class="ic">${faceHTML(member, 28)}</span><span class="tx"><span class="nm">${esc(skill.name)}</span><span class="ds">${esc(member.name)} · ${esc(skill.description)}</span></span>`,
      "cs-open",
      ` data-conn="skill-open"${ids} data-k="cs-skill:${key}" aria-haspopup="dialog" title="${esc(t("conn.skill.open", { p0: skill.name }))}"`,
    ) +
    tg(skill.enabled, t("conn.skill.switch", { p0: skill.name }), ` data-conn="skill-enable"${ids} data-k="cs-skill-enable:${key}"`) +
    `<span class="chev" aria-hidden="true">${ico("right")}</span></div>`
  );
}

/** The list of skills, under its name. */
export const skillsSectionHTML = (items: SkillItem[]): string =>
  `<section class="cs-sec"><h2>${esc(t("conn.skills"))}</h2><div class="cs-list" data-stagger="skills">${items.map(skillRowHTML).join("")}</div></section>`;

/** Whether the draft is not what was saved: the one reason to enable 「儲存」. */
export const skillChanged = (page: SkillPage): boolean => page.state === "ready" && page.draft !== page.saved;

/**
 * The page of a skill, as a dialog over the tab (the same dialog an app has): the switch is the trigger, on or off; the line under the
 * name says who has it and when it is used (its description, which the document's first lines hold); the instructions are the whole
 * document. 「刪除技能」 is the destructive action on the left, 「儲存」 the primary one.
 */
export function skillDialogHTML(item: SkillItem, page: SkillPage, closeButton: string, retry: string, notice = ""): string {
  const { member, skill } = item;
  const ids = ` data-member="${member.id}" data-id="${skill.id}"`;
  const head =
    `<div class="cs-dlg-head">${faceHTML(member, 40)}<span class="sp"></span>` +
    tg(skill.enabled, t("conn.skill.switch", { p0: skill.name }), ` data-conn="skill-enable"${ids} data-k="cs-skill-dlg-enable"`) +
    closeButton + `</div><h2 class="cs-dlg-name" id="cs-dlg-title">${esc(skill.name)}</h2>` +
    `<p class="cs-desc">${esc(member.name)} · ${esc(skill.description)}</p>`;
  const body = page.state === "loading" ? `<p role="status">${esc(t("conn.loading"))}</p>`
    : page.state === "failed" ? `<div class="note" role="alert">${esc(t("conn.skill.loadFailed"))}${retry}</div>`
    : `<label class="cs-field"><span class="flabel">${esc(t("conn.skill.instructions"))}</span>` +
      `<textarea id="skill-text" class="field cs-skill-text" rows="14" spellcheck="false" data-k="cs-skill-text"${page.busy ? " disabled" : ""}>${esc(page.draft)}</textarea></label>` +
      (page.error ? `<div class="note" role="alert">${st("err", page.error)}</div>` : "");
  const acts = actionRow({
    cls: "cs-acts", sticky: true,
    destructive: { label: esc(t("conn.skill.delete")), attrs: ` data-conn="skill-delete"${ids} data-k="cs-skill-delete"${page.busy ? " disabled" : ""}` },
    primary: { label: esc(t("conn.skill.save")), attrs: ` data-conn="skill-save"${ids} data-k="cs-skill-save"${skillChanged(page) && !page.busy ? "" : " disabled"}` },
  });
  return head + body + notice + acts;
}

/** What a refusal of the core says of a document, in the words of the window; any other failure as it is described elsewhere. */
export function skillErrorText(error: unknown): string {
  const text = error instanceof Error ? error.message : String(error);
  if (/must not be empty|must be text|at most \d+ characters/.test(text) && /name|description/.test(text)) return t("conn.skill.err.header");
  if (/already exists/.test(text)) return t("conn.skill.err.taken");
  if (/too large/.test(text)) return t("conn.skill.err.size");
  if (/UTF-8 Markdown/.test(text)) return t("conn.skill.err.invalid");
  return describeError(error);
}
