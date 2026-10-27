import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { scene, click, wait, waitGone, type, check } from "./steps.mjs";

// The owner's apps and private skills (P15a): the sidebar's 連結應用程式 opens the settings at the one connections tab, where the apps
// that can be called now, the ones that cannot and the members' private skills are one connected view. Every scene enters through the
// real sidebar entry; the IPC double computes the count of connected apps from this data as the core does (mock-ipc.js `inventory`).
const base = JSON.parse(readFileSync(resolve(import.meta.dirname, "../fixture.json"), "utf8"));
const open = [click("#nav .nav-apps"), wait('.modal-scrim [data-pane="connections"][aria-current="page"]'), check("settings-shell")];

const tool = (slug, name, kind, description) => ({ slug, name, description, kind, enabled: true });
const notionTools = [
  tool("NOTION_SEARCH", "搜尋頁面", "read", "在工作區搜尋筆記與資料庫"),
  tool("NOTION_FETCH_PAGE", "讀取頁面", "read", "讀取一個頁面的內容"),
  tool("NOTION_LIST_DATABASES", "列出資料庫", "read", "查看工作區裡的資料庫"),
  tool("NOTION_QUERY_DATABASE", "查詢資料庫", "read", "依條件找出資料庫裡的列"),
  tool("NOTION_LIST_USERS", "列出成員", "read", "查看工作區的成員"),
  tool("NOTION_FETCH_COMMENTS", "讀取留言", "read", "讀取頁面上的留言"),
  tool("NOTION_CREATE_PAGE", "建立頁面", "write", "在指定的位置新增頁面"),
  tool("NOTION_UPDATE_PAGE", "更新頁面", "write", "修改頁面的內容與屬性"),
  tool("NOTION_ADD_COMMENT", "新增留言", "write", "在頁面上留言"),
];
const connector = (id, over) => ({ id, kind: "composio", name: "", description: "", logo: "", status: "ACTIVE", enabled: true, enabled_tools: [], disabled_tools: [], accounts: [{ id: `ca_${id}`, status: "ACTIVE" }], ...over });
const notion = over => connector("composio:notion", { name: "Notion", description: "搜尋筆記、整理頁面與專案資料", enabled_tools: notionTools.map(item => item.slug), ...over });
const google = { ...base.managedConnectors.find(item => item.id === "google"), accounts: [{ id: "me", status: "ACTIVE" }] };

const skillDoc = (name, description, body) => `---\nname: ${name}\ndescription: ${description}\n---\n${body}`;
const weekly = skillDoc("weekly-summary", "每週五整理本週的研究重點，用三段話寫成摘要", "# 每週摘要\n\n1. 先寫結論，一句話。\n2. 列出三個重點，每個重點附上來源。\n3. 最後寫下下週要做的事。\n");
const skills = [
  { id: 7, member_id: 1, name: "weekly-summary", description: "每週五整理本週的研究重點，用三段話寫成摘要", enabled: true, source: "owner/research-skills", text: weekly },
  { id: 8, member_id: 1, name: "source-check", description: "引用資料前先核對來源與日期", enabled: true, source: "owner/research-skills" },
  { id: 9, member_id: 2, name: "meeting-notes", description: "會議結束後整理決議與負責人", enabled: false, source: "owner/team-skills" },
];

export default [
  scene("connect-apps-categories", "連結應用程式 opens the store: search, categories as a dropdown and the recommended apps with their add button", [
    ...open, click('[data-conn="tab"][data-id="store"]'), wait(".cs-card"), click('[data-k="conn-category"]'), wait('#app-popover [data-value="productivity"]'),
  ]),
  scene("connected-apps-skills", "The connected view: apps that can be called now, those that cannot with the reason, and the members' private skills; one count", [
    ...open, wait('[data-k="cs-open:composio:notion"]'), wait('[data-k="cs-skill:1:7"]'), wait('[data-stagger="installed-not"] .cs-item'), check("apps-count"),
  ], {
    managedConnectors: [
      notion(), google,
      connector("composio:gmail", { name: "Gmail", description: "搜尋郵件、撰寫草稿與回覆", enabled_tools: ["GMAIL_FETCH_EMAILS", "GMAIL_CREATE_DRAFT"] }),
      connector("composio:slack", { name: "Slack", description: "搜尋團隊訊息、分享工作進度", status: "INITIALIZING", accounts: [{ id: "ca_slack", status: "INITIALIZING" }] }),
    ],
    servers: base.servers.slice(0, 1),
    skills,
  }),
  scene("connected-apps-unreadable", "A core that cannot list the apps: the sidebar and the tab say 「—」 and give no number, and the apps stay one list", [
    ...open, wait('[data-k="cs-open:composio:notion"]'), check("apps-unreadable"),
  ], { inventoryUnreadable: true, managedConnectors: [notion(), google], servers: base.servers.slice(0, 1), skills }),
  scene("connector-account-tools", "An app's detail: its accounts, how many of its tools are on (9/9), a switch for each tool, sign in again and uninstall", [
    ...open, click('[data-conn="open"][data-id="composio:notion"]'), wait('[data-conn="tool"][data-tool="NOTION_SEARCH"]'), wait(".cs-acct"), check("connector-tools"),
  ], {
    managedConnectors: [notion({ accounts: [{ id: "ca_work", status: "ACTIVE" }, { id: "ca_old", status: "EXPIRED" }] }), google],
    pluginTools: { ...base.pluginTools, "composio:notion": notionTools },
  }),
  scene("connector-uninstalled", "Uninstalling an app on the second press: it leaves the list, and the sidebar's count and the tab's go down together", [
    ...open, click('[data-conn="open"][data-id="composio:notion"]'), click('[data-conn="server-menu"][data-id="composio:notion"]'),
    click('.menu [data-conn="disconnect"]'), click('.menu [data-conn="disconnect"]'), waitGone('[data-k="cs-open:composio:notion"]'), wait(".toast"), check("apps-count"),
  ], {
    managedConnectors: [notion(), google, connector("composio:linear", { name: "Linear", description: "追蹤專案與團隊議題", enabled_tools: ["LINEAR_LIST_ISSUES"] })],
    pluginTools: { ...base.pluginTools, "composio:notion": notionTools },
  }),
  scene("skill-private-detail", "A private skill's page: its name, who has it and when it is used, a switch, its instructions as Markdown to edit, keep, and delete", [
    ...open, click('[data-k="cs-skill:1:7"]'), wait("#skill-text"),
    type("#skill-text", weekly.replace("3. 最後寫下下週要做的事。", "3. 最後寫下下週要做的事，並標出需要主人決定的。")),
    click('[data-conn="skill-save"]'), wait(".toast"), check("skill-page"),
  ], { skills }),
];
