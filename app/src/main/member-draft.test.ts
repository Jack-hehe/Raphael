// @vitest-environment happy-dom
import { afterEach, describe, expect, test, vi } from "vitest";
import { setLocale } from "./i18n";
import {
  createDraftPanel,
  draftGroupChoices,
  hasDrafts,
  isSettledDraftTurn,
  presetUrlOf,
  settledDraftHTML,
} from "./member-draft";
import { emptyTimelineState, turnHTML } from "./timeline";
import type { AgentQuestion, AgentTurn, MemberDraft } from "./types";

afterEach(() => setLocale("zh-TW"));

const sampleDrafts = (): MemberDraft[] => [
  {
    id: "draft-1",
    name: "COO",
    role: "營運長",
    blurb: "負責公司日常營運與進度追蹤。",
    personality: "有條理、務實。",
    instructions: "每週整理進度與待決事項。",
    group: "個人公司",
    avatar_preset: "",
    member_id: null,
  },
  {
    id: "draft-2",
    name: "PM",
    role: "專案經理",
    blurb: "負責產品規劃與需求拆解。",
    personality: "細心、擅長溝通。",
    instructions: "追蹤里程碑與阻礙。",
    group: "個人公司",
    avatar_preset: "",
    member_id: null,
  },
  {
    id: "draft-3",
    name: "RD",
    role: "研發工程師",
    blurb: "負責系統架構與程式實作。",
    personality: "嚴謹、重視品質。",
    instructions: "撰寫乾淨程式碼並通過驗證。",
    group: "個人公司",
    avatar_preset: "",
    member_id: null,
  },
];

const sampleQuestion = (): AgentQuestion => ({
  id: "q-draft-1",
  member_id: 0,
  prefix: "問",
  text: "建立 3 位成員",
  tier: "T2",
  timeout_ms: 30000,
  drafts: sampleDrafts(),
  groups: [{ id: "group-1", name: "個人公司", group_id: null }],
});

describe("member draft helpers", () => {
  test("hasDrafts identifies questions with structured drafts", () => {
    expect(hasDrafts(null)).toBe(false);
    expect(hasDrafts({ id: "q1", prefix: "問", text: "ok?", tier: "T2", timeout_ms: 1000 })).toBe(false);
    expect(hasDrafts(sampleQuestion())).toBe(true);
  });

  test("draftGroupChoices combines ungrouped, existing groups, and proposed new groups without duplicates", () => {
    const choices = draftGroupChoices(
      [{ id: 1, name: "專案工作", position: 0, sources: [] }],
      [{ id: "group-1", name: "個人公司", group_id: null }],
      sampleDrafts(),
    );
    expect(choices.map((c) => c.label)).toEqual(["未分組", "專案工作", "個人公司"]);
  });

  test("presetUrlOf resolves preset stem or full preset:<stem>", () => {
    const presets = [{ avatar: "preset:1", name: "1", url: "data:image/png;base64,AAAA" }];
    expect(presetUrlOf("preset:1", presets)).toBe("data:image/png;base64,AAAA");
    expect(presetUrlOf("1", presets)).toBe("data:image/png;base64,AAAA");
    expect(presetUrlOf("", presets)).toBeNull();
  });
});

describe("DraftPanelController", () => {
  test("renders cards, edits fields in place, removes a draft, and submits edited drafts", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    const panel = createDraftPanel({
      groups: () => [{ id: 1, name: "專案工作", position: 0, sources: [] }],
      presets: () => [],
      onConfirm,
      onCancel,
    });

    panel.sync(sampleQuestion());
    expect(panel.el.hidden).toBe(false);
    expect(panel.el.querySelectorAll(".draft-card")).toHaveLength(3);
    expect(panel.el.querySelector('[data-draft-act="confirm"]')?.textContent).toBe("建立 3 位成員");

    // Edit the first draft's role and blurb
    const firstCard = panel.el.querySelector<HTMLElement>('[data-draft-id="draft-1"]')!;
    const roleInput = firstCard.querySelector<HTMLInputElement>('[data-draft-field="role"]')!;
    roleInput.value = "首席營運長";
    roleInput.dispatchEvent(new Event("input", { bubbles: true }));

    // Remove the third draft (RD)
    const thirdRemove = panel.el.querySelector<HTMLButtonElement>('[data-draft-id="draft-3"] [data-draft-act="remove"]')!;
    thirdRemove.click();

    expect(panel.el.querySelectorAll(".draft-card")).toHaveLength(2);
    expect(panel.el.querySelector('[data-draft-act="confirm"]')?.textContent).toBe("建立 2 位成員");

    // Confirm
    panel.el.querySelector<HTMLButtonElement>('[data-draft-act="confirm"]')!.click();
    expect(onConfirm).toHaveBeenCalledTimes(1);
    const submitted = onConfirm.mock.calls[0]![0] as MemberDraft[];
    expect(submitted.map((d) => d.name)).toEqual(["COO", "PM"]);
    expect(submitted[0]!.role).toBe("首席營運長");
  });
});

describe("settled draft summary in timeline", () => {
  const baseTurn: AgentTurn = {
    id: 99,
    created_at_ms: 1792807154000,
    question: "幫我建立個人公司團隊",
    route: "answer",
    prefix: "答",
    text: "已建立 COO、PM、RD",
    detail: "COO · 首席營運長 · 個人公司 — 負責公司日常營運與進度追蹤。\nPM · 專案經理 · 個人公司 — 負責產品規劃與需求拆解。\nRD · 研發工程師 · 個人公司 — 負責系統架構與程式實作。",
    status: "done",
    messages: [
      { seq: 1, kind: "thinking", text: "正在規劃成員設定。", at_ms: 1792807155000 },
      { seq: 2, kind: "reply", text: "已建立 COO、PM、RD", at_ms: 1792807160000 },
    ],
  };

  test("isSettledDraftTurn detects settled batch turns and renders expandable one-line summary without duplicate bubble", () => {
    expect(isSettledDraftTurn(baseTurn)).toBe(true);
    const closed = turnHTML(baseTurn, emptyTimelineState(), false, { face: () => "" });
    expect(closed).toContain('class="draft-done"');
    expect(closed).toContain('data-act="draft-done" aria-expanded="false"');
    expect(closed).toContain("已建立 COO、PM、RD");
    expect(closed).toContain('id="draft-done-99" hidden');
    // No duplicate .ans reply bubble for the summary text
    expect(closed).not.toContain("《答。已建立 COO、PM、RD》");

    const opened = settledDraftHTML(baseTurn, true);
    expect(opened).toContain('class="draft-done is-open"');
    expect(opened).toContain('aria-expanded="true"');
    expect(opened).toContain("COO · 首席營運長 · 個人公司");
  });
});
