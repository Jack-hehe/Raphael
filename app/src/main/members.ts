// The members of the 拉斐爾 page, as data: Raphael (member 0, who is no row of the core) and the
// members the owner made, in their groups. Everything here is pure, so its rules can be tested
// without a browser: which members the column lists under which group for a search, what a row's
// preview says, how a routine's schedule is said, what a member's picture is, and what a group's
// 看的地方 are made of.
//
// A member's picture is the image the core hands as a `data:image/...` URL (`avatar_url`, the way
// Raphael's own picture comes from `avatar_get`), and only that: anything else is the neutral
// silhouette on `--surface-2`. Raphael's face is the emblem or
// his picture (avatar.ts), never the placeholder.

import { avatarHTML } from "./avatar";
import { catalogs, t } from "./i18n";
import type { MessageKey } from "./i18n/zh-TW";
import type { Member, MemberGroup, MemberSource, MembersList, RoutineSchedule } from "./types";
import { esc, ico } from "./ui";

/** Raphael's id among the members: member 0 everywhere in the core's API. */
export const RAPHAEL_ID = 0;

/** The longest name, role, instructions and group name the core takes (members.* and groups.*). */
export const NAME_MAX = 16;
export const ROLE_MAX = 24;
export const PERSONALITY_MAX = 2000;
export const INSTRUCTIONS_MAX = 2000;
export const GROUP_NAME_MAX = 24;
/** The longest title and prompt of a routine (routines.*). */
export const ROUTINE_TITLE_MAX = 40;
export const ROUTINE_PROMPT_MAX = 1000;

/** The composer's placeholder in a member's thread: the same English words in every language, as "Ask Raphael" is. */
export const memberPlaceholder = (name: string): string => `Message ${name}`;

/** A one-line field (a member's name or role, or a group's name) as the core stores it: trimmed, with inner whitespace collapsed. */
export const cleanName = (value: string): string => value.trim().replace(/\s+/g, " ");

/** Whether `name` is already taken by another member or matches Raphael's reserved name in any language. */
export function isMemberNameTaken(name: string, taken: readonly string[]): boolean {
  const key = cleanName(name).toLocaleLowerCase();
  const reserved = Object.values(catalogs).map((c) => cleanName(c["nav.agent"]).toLocaleLowerCase());
  return reserved.includes(key) || taken.some((other) => cleanName(other).toLocaleLowerCase() === key);
}

/** Text as the search compares it: the same width and case for every way of typing it. */
const fold = (text: string): string => text.normalize("NFKC").toLocaleLowerCase();

/** Whether a member answers the column's search: in its name or its role. Blank matches all. */
export function memberMatches(member: Pick<Member, "name" | "role" | "blurb">, query: string): boolean {
  const q = fold(query.trim());
  return q === "" || fold(member.name).includes(q) || fold(member.role ?? "").includes(q) || fold(member.blurb ?? "").includes(q);
}

/** A section of the column: a group (null: 未分組) and its members that answer the search. */
export interface MemberSection {
  group: MemberGroup | null;
  members: Member[];
}

/**
 * The column under Raphael: the groups in their order, then 未分組, each with its members in the
 * order the core lists them. With a search, only the members that answer it, and only the sections
 * that keep one; without, every group, an empty one too (so that it can be filled), and 未分組 only
 * when it holds someone. A member whose group is gone is 未分組.
 */
export function memberSections(list: MembersList, query = ""): MemberSection[] {
  const searching = query.trim() !== "";
  const groups = [...list.groups].sort((a, b) => a.position - b.position || a.id - b.id);
  const known = new Set(groups.map((group) => group.id));
  const people = list.members.filter((member) => member.id !== RAPHAEL_ID && !member.hidden && memberMatches(member, query));
  const sections: MemberSection[] = groups.map((group) => ({ group, members: people.filter((member) => member.group_id === group.id) }));
  sections.push({ group: null, members: people.filter((member) => member.group_id === null || !known.has(member.group_id)) });
  return sections.filter((section) => (searching || section.group === null ? section.members.length > 0 : true));
}

/** Raphael as the list gives him, or a stand-in with his name before the list is read. */
export function raphaelOf(list: MembersList | null): Member {
  const found = list?.members.find((member) => member.id === RAPHAEL_ID);
  return (
    found ?? {
      id: RAPHAEL_ID, name: t("agent.title"), role: "", instructions: "", avatar: "", group_id: null, model: "", tools: null,
      position: -1, created: 0, unread: false, waiting: false, preview: "", last_at: null,
    }
  );
}

/** The member `id` of the list, if there is one. Raphael always is. */
export const memberOf = (list: MembersList | null, id: number): Member | undefined =>
  id === RAPHAEL_ID ? raphaelOf(list) : list?.members.find((member) => member.id === id);

/**
 * A row's second line: the last line of its thread (`preview`), or, while a question waits for the
 * owner there, that line in gold after 「等你處理：」 (the waiting turn is the thread's last, so its
 * line is what is asked), 「等你處理」 alone when there is none.
 */
export function previewOf(member: Member, last: string | undefined): { text: string; waiting: boolean } {
  const line = (last ?? "").replace(/\s+/g, " ").trim();
  if (member.waiting) return { text: line ? t("members.waiting", { p0: line }) : t("members.waitingShort"), waiting: true };
  return { text: line, waiting: false };
}

// ---------- pictures ----------

/** The only pictures a member may have on the page: a data URL of one of the four kinds the app accepts. */
const DATA_URL = /^data:image\/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/]+=*$/;

/** Whether `url` is a picture the page may show as it is. */
export const isPictureUrl = (url: unknown): url is string => typeof url === "string" && DATA_URL.test(url);

/** The placeholder's character: the first of the name (a whole character, an emoji too), or nothing. */
export const initialOf = (name: string): string => Array.from(name.trim())[0] ?? "";

/**
 * A face at `size` px: Raphael's emblem or picture (avatar.ts, bare, never cut into a frame of
 * ours), a member's picture in a circle, or a neutral person silhouette.
 * Decorative: whoever shows it names it.
 */
export function faceHTML(member: { id: number; name: string; avatar_url?: string | null }, size: number, o: { loop?: boolean; cls?: string } = {}): string {
  if (member.id === RAPHAEL_ID) return avatarHTML({ size, loop: o.loop, cls: o.cls });
  const cls = `av mb-av${o.cls ? ` ${o.cls}` : ""}`;
  const box = `style="--av:${Math.round(size)}px"`;
  if (isPictureUrl(member.avatar_url)) {
    return `<span class="${cls} mb-av-pic" ${box} aria-hidden="true"><img src="${esc(member.avatar_url)}" alt="" draggable="false"></span>`;
  }
  return `<span class="${cls} mb-av-placeholder" ${box} aria-hidden="true">${ico("person")}</span>`;
}

// ---------- routines ----------

const DAY_KEYS: readonly MessageKey[] = ["members.day0", "members.day1", "members.day2", "members.day3", "members.day4", "members.day5", "members.day6"];
const tr = t as unknown as (key: MessageKey) => string;

/** The name of day `n` of the week, 0 being Sunday. */
export const dayName = (n: number): string => tr(DAY_KEYS[((n % 7) + 7) % 7]);

/** A time of day as the core keeps it: `HH:MM`, 24 hours. */
export const isTime = (text: string): boolean => /^([01]\d|2[0-3]):[0-5]\d$/.test(text);

/** How a routine's schedule is said in a row: 「每天 09:00」, 「每週日 20:00」, 「每 3 小時」. */
export function scheduleText(schedule: RoutineSchedule): string {
  if (schedule.kind === "once") return t("auto.onceAt", { p0: schedule.start_date ?? "", p1: schedule.time });
  if (schedule.kind === "weekdays") return t("auto.weekdaysAt", { p0: schedule.time });
  if (schedule.kind === "daily") return t("members.dailyAt", { p0: schedule.time });
  if (schedule.kind === "weekly") {
    const days = [...new Set(schedule.days)].sort((a, b) => a - b).map(dayName).join(t("members.daysJoin"));
    return t("members.weeklyAt", { p0: days, p1: schedule.time });
  }
  return schedule.hours === 1 ? t("members.everyHour") : t("members.everyHours", { p0: schedule.hours });
}

/** The intervals the 每隔幾小時 dropdown offers, in hours. */
export const HOUR_STEPS: readonly number[] = [1, 2, 3, 4, 6, 8, 12, 24];

// ---------- group sources ----------

/**
 * A place typed for 看的地方: a GitHub `owner/repo` (or the address of one), or a Notion page's
 * address or id. Null for anything else. A GitHub address becomes its `owner/repo`; a Notion one
 * is kept as typed (the core takes either).
 */
export function parseSource(text: string): MemberSource | null {
  const typed = text.trim();
  if (typed === "") return null;
  const github = /^(?:https?:\/\/)?(?:www\.)?github\.com\/([\w.-]+)\/([\w.-]+?)(?:\.git)?(?:[/?#].*)?$/i.exec(typed);
  if (github) return { kind: "github", ref: `${github[1]}/${github[2]}` };
  if (/^[\w.-]+\/[\w.-]+$/.test(typed) && !/^\.|\/\./.test(typed)) return { kind: "github", ref: typed };
  if (/^(?:https?:\/\/)?(?:[\w-]+\.)?notion\.(?:so|site)\/\S+$/i.test(typed)) return { kind: "notion", ref: typed };
  if (/^[0-9a-f]{32}$/i.test(typed.replace(/-/g, ""))) return { kind: "notion", ref: typed };
  return null;
}

/** How a place is shown on its chip: `owner/repo`, or a Notion page by the words of its address. */
export function sourceLabel(source: MemberSource): string {
  if (source.kind === "github") return source.ref;
  const path = source.ref.replace(/^(?:https?:\/\/)?[^/]+\//, "").split(/[?#]/)[0];
  const last = path.split("/").filter(Boolean).pop() ?? source.ref;
  const words = last.replace(/-?[0-9a-f]{32}$/i, "").replace(/-/g, " ").trim();
  return words || "Notion";
}
