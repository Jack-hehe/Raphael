// The personalization card of the home page: which kinds of programs the person dictates into. The
// core counts dictations per program (`stats.summary.by_app`, process names such as "Code.exe");
// this module sorts the programs into six kinds and works out their shares. Pure: the words for the
// kinds and for what Raphael says about them are in the catalogue (the card puts them together).

import type { AppUsage } from "./types";

export type CategoryId = "ai" | "dev" | "docs" | "chat" | "web" | "other";

/** The kinds in the order the chart lays them out, clockwise from the top. "other" is last. */
export const CATEGORY_IDS: readonly CategoryId[] = ["ai", "dev", "docs", "chat", "web", "other"];

/** `Code.exe` -> `code`, `WINWORD.EXE` -> `winword`, `ms-teams.exe` -> `msteams`; a path is cut to the file. */
export function programKey(app: string): string {
  return app
    .trim()
    .replace(/^.*[\\/]/, "")
    .toLowerCase()
    .replace(/\.exe$/, "")
    .replace(/[\s._-]+/g, "");
}

const set = (...names: string[]): ReadonlySet<string> => new Set(names);

const AI = set("chatgpt", "claude", "perplexity", "copilot", "gemini", "grok", "deepseek", "kimi", "doubao", "poe", "lmstudio", "ollama", "chatbox", "cherrystudio", "codex");
const DEV = set(
  "code", "codeinsiders", "cursor", "windsurf", "zed", "devenv", "idea", "idea64", "pycharm", "pycharm64", "webstorm", "webstorm64",
  "clion", "clion64", "rider", "rider64", "goland", "goland64", "rustrover", "rustrover64", "phpstorm", "phpstorm64", "datagrip",
  "datagrip64", "rubymine", "rubymine64", "studio64", "sublimetext", "notepad++", "windowsterminal", "wt", "powershell", "pwsh", "cmd",
  "conhost", "openconsole", "weztermgui", "alacritty", "mintty", "gitbash", "putty", "kitty", "tabby", "hyper", "mobaxterm", "githubdesktop",
  "gitkraken", "sourcetree", "postman", "insomnia", "dockerdesktop", "gvim", "nvimqt", "emacs",
);
const DOCS = set(
  "winword", "excel", "powerpnt", "onenote", "msaccess", "mspub", "visio", "notion", "obsidian", "evernote", "logseq", "typora", "marktext",
  "notepad", "wordpad", "acrobat", "acrord32", "sumatrapdf", "foxitreader", "foxitpdfreader", "wps", "et", "wpp", "soffice", "joplin", "zotero", "xmind",
);
const CHAT = set(
  "line", "discord", "slack", "teams", "msteams", "telegram", "whatsapp", "wechat", "weixin", "signal", "skype", "zoom", "outlook", "olk",
  "hxoutlook", "thunderbird", "mattermost", "kakaotalk", "messenger", "dingtalk", "feishu", "lark", "wxwork", "wecom", "element", "viber", "chatwork", "webex",
);
const WEB = set(
  "chrome", "msedge", "firefox", "brave", "opera", "vivaldi", "arc", "iexplore", "thorium", "librewolf", "zen", "chromium", "waterfox",
  "browser", "maxthon", "floorp",
);

/** The kind of program `app` is (a process name; anything not known is "other"). */
export function categoryOf(app: string): CategoryId {
  const key = programKey(app);
  if (AI.has(key)) return "ai";
  if (DEV.has(key)) return "dev";
  if (DOCS.has(key)) return "docs";
  if (CHAT.has(key)) return "chat";
  if (WEB.has(key)) return "web";
  return "other";
}

/** How a program is called on the card where the process name is not a good name. */
const NAMES: Readonly<Record<string, string>> = {
  code: "VS Code",
  codeinsiders: "VS Code Insiders",
  devenv: "Visual Studio",
  idea: "IntelliJ IDEA",
  idea64: "IntelliJ IDEA",
  pycharm: "PyCharm",
  pycharm64: "PyCharm",
  webstorm: "WebStorm",
  webstorm64: "WebStorm",
  clion: "CLion",
  clion64: "CLion",
  rider: "Rider",
  rider64: "Rider",
  goland: "GoLand",
  goland64: "GoLand",
  rustrover: "RustRover",
  rustrover64: "RustRover",
  phpstorm: "PhpStorm",
  phpstorm64: "PhpStorm",
  studio64: "Android Studio",
  windowsterminal: "Windows Terminal",
  wt: "Windows Terminal",
  openconsole: "Windows Terminal",
  conhost: "conhost",
  pwsh: "PowerShell",
  cmd: "cmd",
  weztermgui: "WezTerm",
  gitbash: "Git Bash",
  githubdesktop: "GitHub Desktop",
  gitkraken: "GitKraken",
  dockerdesktop: "Docker Desktop",
  sublimetext: "Sublime Text",
  winword: "Word",
  powerpnt: "PowerPoint",
  msaccess: "Access",
  mspub: "Publisher",
  onenote: "OneNote",
  acrord32: "Acrobat Reader",
  sumatrapdf: "SumatraPDF",
  soffice: "LibreOffice",
  msteams: "Teams",
  teams: "Teams",
  olk: "Outlook",
  hxoutlook: "Outlook",
  line: "LINE",
  chatgpt: "ChatGPT",
  lmstudio: "LM Studio",
  msedge: "Edge",
  iexplore: "Internet Explorer",
  kakaotalk: "KakaoTalk",
  wechat: "WeChat",
  weixin: "WeChat",
  whatsapp: "WhatsApp",
  dingtalk: "DingTalk",
};

/** The name of a program for people: `Code.exe` is "VS Code", `slack.exe` "Slack", `Figma.exe` "Figma". */
export function appLabel(app: string): string {
  const key = programKey(app);
  const known = NAMES[key];
  if (known) return known;
  const bare = app.trim().replace(/^.*[\\/]/, "").replace(/\.exe$/i, "");
  return bare === bare.toLowerCase() ? bare.charAt(0).toUpperCase() + bare.slice(1) : bare;
}

/**
 * Whole percentages that add up to 100 (the largest remainder method), for weights that may not:
 * 1/3 each is 34, 33, 33 and not three times 33. Nothing at all gives zeros.
 */
export function percentages(weights: readonly number[]): number[] {
  const total = weights.reduce((sum, w) => sum + w, 0);
  if (!(total > 0)) return weights.map(() => 0);
  const exact = weights.map((w) => (w / total) * 100);
  const floors = exact.map(Math.floor);
  let left = 100 - floors.reduce((sum, n) => sum + n, 0);
  const order = exact
    .map((value, index) => ({ index, rest: value - Math.floor(value) }))
    .sort((a, b) => b.rest - a.rest || a.index - b.index);
  for (const { index } of order) {
    if (left <= 0) break;
    floors[index]++;
    left--;
  }
  return floors;
}

export interface CategoryShare {
  id: CategoryId;
  /** How much was dictated into programs of this kind (words, or dictations when no words were counted). */
  weight: number;
  /** A whole percentage; all of them add up to 100. */
  percent: number;
  /** The names of the (at most two) programs of this kind with the most. */
  apps: string[];
}

export interface Personalization {
  /** What `weight` counts. */
  measure: "words" | "dictations";
  /** Every kind, in the order of `CATEGORY_IDS`, those without anything too. */
  categories: CategoryShare[];
}

const amount = (n: unknown): number => (typeof n === "number" && Number.isFinite(n) && n > 0 ? n : 0);

/**
 * Sorts the programs of `stats.summary.by_app` into kinds and works out the shares. Null when there
 * is nothing to show: an older core without `by_app`, or no dictation in the last 30 days.
 */
export function personalize(rows: readonly AppUsage[] | undefined): Personalization | null {
  if (!Array.isArray(rows)) return null;
  const usable = rows.filter((row) => row && typeof row.app === "string");
  // Words are what the whole page counts; a core that counted none (it can only be dictations) falls back to them.
  const measure = usable.some((row) => amount(row.words) > 0) ? "words" : "dictations";
  const weightOf = (row: AppUsage): number => amount(measure === "words" ? row.words : row.dictations);
  const weights = new Map<CategoryId, number>(CATEGORY_IDS.map((id) => [id, 0]));
  const perApp = new Map<CategoryId, Map<string, number>>(CATEGORY_IDS.map((id) => [id, new Map()]));
  for (const row of usable) {
    const weight = weightOf(row);
    if (weight === 0) continue;
    const id = categoryOf(row.app);
    weights.set(id, (weights.get(id) ?? 0) + weight);
    // "unknown" is the history not knowing, not a program with a name
    if (programKey(row.app) !== "unknown") {
      const names = perApp.get(id)!;
      const label = appLabel(row.app);
      names.set(label, (names.get(label) ?? 0) + weight);
    }
  }
  const all = CATEGORY_IDS.map((id) => weights.get(id) ?? 0);
  if (!all.some((w) => w > 0)) return null;
  const shares = percentages(all);
  return {
    measure,
    categories: CATEGORY_IDS.map((id, i) => ({
      id,
      weight: all[i],
      percent: shares[i],
      apps: [...perApp.get(id)!]
        .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
        .slice(0, 2)
        .map(([name]) => name),
    })),
  };
}

/** What Raphael says about the shares. `most` and `half` name the kind that leads. */
export type Insight =
  | { kind: "spread" }
  | { kind: "most"; id: CategoryId; percent: number }
  | { kind: "half"; id: CategoryId; percent: number };

/** From this share on, one kind is "most" of the dictation; from half on, "more than half". Under it, it is spread out. */
export const LEAD_FROM = 35;
export const HALF_FROM = 50;

/** The one thing worth saying: a kind that leads clearly, or that it is spread. Unknown programs never lead. */
export function insightOf(p: Personalization): Insight {
  const named = p.categories.filter((c) => c.id !== "other");
  const lead = named.reduce((best, c) => (c.percent > best.percent ? c : best), named[0]);
  if (!lead || lead.percent < LEAD_FROM) return { kind: "spread" };
  return { kind: lead.percent >= HALF_FROM ? "half" : "most", id: lead.id, percent: lead.percent };
}
