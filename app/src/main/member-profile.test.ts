// @vitest-environment happy-dom
import { beforeEach, expect, test, vi } from "vitest";
import * as api from "./api";
import { setLocale } from "./i18n";
import { openEditMemberDialog, openMemberProfile, type MemberProfileHost } from "./member-profile";
import { emptyProfileDraft, flushMemberProfileDraft, mountMemberResources } from "./member-profile-resources";
import { memberPreset, randomMemberName } from "./member-profile-presets";
import type { Member } from "./types";

vi.mock("./api", () => ({ core: vi.fn(), membersCreate: vi.fn(), membersUpdate: vi.fn(), membersSetAvatar: vi.fn(), membersList: vi.fn(), membersSkillsList: vi.fn(), membersSkillsImport: vi.fn(), membersSkillsUpdate: vi.fn(), membersSkillsDelete: vi.fn(), membersCommandsList: vi.fn(), membersCommandsSave: vi.fn(), membersCommandsDelete: vi.fn(), membersVoices: vi.fn(), routinesList: vi.fn(), routinesCreate: vi.fn(), routinesUpdate: vi.fn(), routinesDelete: vi.fn() }));
const member: Member = { id: 7, name: "Mia", role: "Research", instructions: "Keep sources", personality: "Careful", avatar: "", group_id: null, model: "", tools: null, position: 0, created: 0, unread: false, waiting: false, preview: "", last_at: null };
const flush = async (): Promise<void> => { for (let i = 0; i < 25; i++) await Promise.resolve(); };
const host = (over: Partial<MemberProfileHost> = {}): MemberProfileHost => ({ groups: [], presets: [], taken: [], create: vi.fn(async () => member), ...over });
const click = (root: ParentNode, selector: string): void => root.querySelector<HTMLElement>(selector)!.click();
const fill = (root: ParentNode, selector: string, text: string): void => { const field = root.querySelector<HTMLInputElement>(selector)!; field.value = text; field.dispatchEvent(new Event("input", { bubbles: true })); };

beforeEach(() => {
  document.body.innerHTML = "";
  vi.resetAllMocks();
  setLocale("zh-TW");
  vi.mocked(api.membersSkillsList).mockResolvedValue({ skills: [] });
  vi.mocked(api.membersCommandsList).mockResolvedValue({ commands: [] });
  vi.mocked(api.membersVoices).mockResolvedValue({ voices: [{ id: "fish:1", name: "Voice 1" }], configured: true });
  vi.mocked(api.core).mockResolvedValue({ memories: [] });
  vi.mocked(api.routinesList).mockResolvedValue({ routines: [] });
});

test("tabs retain fields and keyboard navigation selects a visible panel", () => {
  const dialog = openMemberProfile(host());
  fill(dialog.el, "#mb-new-name", "Ada");
  const tab = dialog.el.querySelector<HTMLElement>('#mp-tab-identity')!;
  tab.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
  expect(dialog.el.querySelector('#mp-page-soul')!.hasAttribute("hidden")).toBe(false);
  fill(dialog.el, "#mb-new-instr", "12345");
  expect(dialog.el.querySelector(".mp-counter")!.textContent).toBe("5 / 2000");
  click(dialog.el, '#mp-tab-identity');
  expect(dialog.el.querySelector<HTMLInputElement>("#mb-new-name")!.value).toBe("Ada");
  dialog.close();
});

test("a blank member has a default silhouette and every pane groups content in cards", async () => {
  const dialog = openMemberProfile(host());
  expect(dialog.el.querySelector('.mb-new-av .mp-person')).not.toBeNull();
  fill(dialog.el, '#mb-new-name', 'Ada');
  expect(dialog.el.querySelector('.mb-new-av .mp-person')).not.toBeNull();
  for (const tab of ['identity', 'soul', 'skills', 'memory', 'routines', 'permissions', 'voice']) {
    click(dialog.el, `#mp-tab-${tab}`); await flush();
    expect(dialog.el.querySelector(`#mp-page-${tab} .mp-card`)).not.toBeNull();
  }
  expect(dialog.el.querySelector('#mp-memory')!.closest('.mp-card')).not.toBeNull();
  expect(dialog.el.querySelector('[data-dlg="routine-add"]')!.closest('.mp-card')).not.toBeNull();
  expect(dialog.el.querySelector('[data-dlg="ask"]')!.closest('.mp-resource')!.querySelector('p')!.textContent).toBeTruthy();
  dialog.close();
});

test("profile routines use the shared editor with every recurrence and HH:MM validation", async () => {
  const dialog = openMemberProfile(host());
  click(dialog.el, '#mp-tab-routines'); await flush();
  click(dialog.el, '[data-dlg="routine-add"]');
  const form = document.querySelector<HTMLElement>('.au-routine-dialog')!;
  expect(form.querySelectorAll('[data-dlg="kind"]')).toHaveLength(5);
  expect(form.querySelector<HTMLInputElement>('#au-time')!.type).toBe('text');
  fill(form, '#au-title', 'Review'); fill(form, '#au-prompt', 'Review updates');
  fill(form, '#au-time', '25:60'); click(form, '[data-dlg="save"]'); await flush();
  expect(form.isConnected).toBe(true);
  expect(api.routinesCreate).not.toHaveBeenCalled();
  click(form, '[data-kind="weekly"]');
  expect(form.querySelectorAll('[data-dlg="day"]')).toHaveLength(7);
  click(form, '[data-dlg="cancel"]'); dialog.close();
});

test("localized role presets fill all identity and instruction fields with a suggested command", async () => {
  const dialog = openMemberProfile(host());
  click(dialog.el, '[data-dlg="preset"]');
  click(document, '#app-popover [data-value="research"]');
  expect(dialog.el.querySelector<HTMLInputElement>("#mb-new-role")!.value).toBe("研究夥伴");
  expect(dialog.el.querySelector<HTMLInputElement>("#mp-blurb")!.value).toContain("資料");
  expect(dialog.el.querySelector<HTMLTextAreaElement>("#mb-new-instr")!.value).toContain("第一手");
  click(dialog.el, '#mp-tab-skills');
  expect(dialog.el.textContent).toContain("/research");
  dialog.close();
  setLocale("ja"); expect(memberPreset("research").role).toBe("リサーチャー");
  expect(randomMemberName("female", [])).toMatch(/[\u3040-\u9fff]/);
  setLocale("en"); expect(memberPreset("coding").role).toBe("Coding partner");
  expect(randomMemberName("male", [])).toMatch(/^[A-Za-z]+$/);
});

test("a preset in edit mode suggests a command without writing until its explicit save", async () => {
  const dialog = openEditMemberDialog(host(), member);
  click(dialog.el, '[data-dlg="preset"]'); click(document, '#app-popover [data-value="coding"]');
  click(dialog.el, '#mp-tab-skills'); await flush();
  expect(dialog.el.querySelector<HTMLInputElement>('#mp-command-name')!.value).toBe('coding');
  expect(dialog.el.querySelector<HTMLTextAreaElement>('#mp-command-template')!.value).toContain('{input}');
  expect(api.membersCommandsSave).not.toHaveBeenCalled();
  dialog.close();
});

test("canceling a new member discards skill, command and memory drafts without any writes", async () => {
  const dialog = openMemberProfile(host());
  click(dialog.el, '#mp-tab-skills');
  fill(dialog.el, '#mp-skill-source', 'owner/repo'); click(dialog.el, '[data-res="import"]'); await flush();
  fill(dialog.el, '#mp-command-name', 'review'); fill(dialog.el, '#mp-command-template', 'Review {input}');
  click(dialog.el, '[data-res="command-save"]'); await flush();
  click(dialog.el, '#mp-tab-memory'); await flush();
  fill(dialog.el, '#mp-memory', 'Prefer primary sources'); click(dialog.el, '[data-dlg="memory-add"]'); await flush();
  click(dialog.el, '[data-dlg="cancel"]');
  expect(api.membersSkillsImport).not.toHaveBeenCalled();
  expect(api.membersCommandsSave).not.toHaveBeenCalled();
  expect(api.core).not.toHaveBeenCalled();
  expect(dialog.el.isConnected).toBe(false);
});

test("subresources write only after creation, and a failed write retries without creating again", async () => {
  const create = vi.fn(async () => member);
  vi.mocked(api.membersCommandsSave).mockRejectedValueOnce(new Error("offline")).mockResolvedValue({});
  const dialog = openMemberProfile(host({ create }));
  fill(dialog.el, '#mb-new-name', 'Mia');
  click(dialog.el, '#mp-tab-skills');
  fill(dialog.el, '#mp-command-name', 'review'); fill(dialog.el, '#mp-command-template', 'Review {input}');
  click(dialog.el, '[data-res="command-save"]'); await flush();
  expect(api.membersCommandsSave).not.toHaveBeenCalled();
  click(dialog.el, '[data-dlg="create"]'); await flush();
  expect(create).toHaveBeenCalledTimes(1);
  expect(dialog.el.isConnected).toBe(true);
  click(dialog.el, '[data-dlg="create"]'); await flush();
  expect(create).toHaveBeenCalledTimes(1);
  expect(api.membersCommandsSave).toHaveBeenLastCalledWith(7, { name: "review", description: "", template: "Review {input}" });
  expect(dialog.el.isConnected).toBe(false);
});

test("editing then closing does not save main fields or voice switches", async () => {
  const update = vi.fn(async () => true);
  const dialog = openEditMemberDialog(host({ update }), member);
  fill(dialog.el, '#mb-new-name', 'Changed');
  click(dialog.el, '#mp-tab-voice'); await flush();
  click(dialog.el, '[data-res="notify"]'); await flush();
  click(dialog.el, '[data-dlg="cancel"]');
  expect(update).not.toHaveBeenCalled();
  expect(api.membersUpdate).not.toHaveBeenCalled();
});

test("editing saves reply style, blurb and permission through the footer", async () => {
  const update = vi.fn(async () => true);
  const dialog = openEditMemberDialog(host({ update, taken: ['Mia'] }), member, "soul");
  fill(dialog.el, '#mp-blurb', 'A useful teammate');
  click(dialog.el, '[data-dlg="reply"]'); click(document, '#app-popover [data-value="conversational"]');
  click(dialog.el, '#mp-tab-permissions'); click(dialog.el, '[data-dlg="ask"]');
  click(dialog.el, '[data-dlg="save"]'); await flush();
  expect(update).toHaveBeenCalledWith(7, expect.objectContaining({ reply_style: 'conversational', blurb: 'A useful teammate', ask_before_members: true }));
});

test("resource editor updates existing skills and edits command fields without resetting them", async () => {
  vi.mocked(api.membersSkillsList).mockResolvedValue({ skills: [{ id: 4, name: 'Research', description: 'Sources', enabled: true, source: 'owner/repo' }] });
  vi.mocked(api.membersCommandsList).mockResolvedValue({ commands: [{ id: 6, name: 'review', description: 'Review code', template: 'Review {input}' }] });
  const container = document.createElement('div'); document.body.append(container);
  const editor = mountMemberResources(container, { memberId: 7, tab: 'skills' }); await flush();
  click(container, '[data-res="skill-toggle"]'); await flush();
  expect(api.membersSkillsUpdate).toHaveBeenCalledWith(7, 4, false);
  click(container, '[data-res="command-edit"]');
  expect(container.querySelector<HTMLInputElement>('#mp-command-name')!.value).toBe('review');
  expect(container.querySelector<HTMLTextAreaElement>('#mp-command-template')!.value).toBe('Review {input}');
  fill(container, '#mp-command-template', 'Changed {input}');
  click(container, '[data-res="command-save"]'); await flush();
  expect(api.membersCommandsSave).toHaveBeenCalledWith(7, { id: 6, name: 'review', description: 'Review code', template: 'Changed {input}' });
  editor.destroy();
});

test("resource refresh ignores late responses after destroy", async () => {
  let finish!: (answer: { commands: [] }) => void;
  vi.mocked(api.membersCommandsList).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  const container = document.createElement('div');
  const editor = mountMemberResources(container, { memberId: 7, tab: 'commands' });
  editor.destroy(); const before = container.innerHTML;
  finish({ commands: [] }); await flush();
  expect(container.innerHTML).toBe(before);
});

test("the resource flush removes successful entries before a later failure", async () => {
  const draft = emptyProfileDraft();
  draft.commands.push({ id: -1, name: 'a', description: '', template: '{input}' }, { id: -2, name: 'b', description: '', template: '{input}' });
  vi.mocked(api.membersCommandsSave).mockResolvedValueOnce({}).mockRejectedValueOnce(new Error('offline'));
  await expect(flushMemberProfileDraft(7, draft)).rejects.toThrow('offline');
  expect(draft.commands.map(c => c.name)).toEqual(['b']);
});

test("a skill activation failure retries the imported ids without importing duplicate files", async () => {
  const draft = emptyProfileDraft();
  draft.skills.push({ id: -1, name: 'Skill', source: 'owner/repo', description: '', enabled: false });
  vi.mocked(api.membersSkillsImport).mockResolvedValue({ skill: { id: 9, name: 'Skill', source: 'owner/repo', description: '', enabled: true } });
  vi.mocked(api.membersSkillsUpdate).mockRejectedValueOnce(new Error('offline')).mockResolvedValue({});
  await expect(flushMemberProfileDraft(7, draft)).rejects.toThrow('offline');
  await flushMemberProfileDraft(7, draft);
  expect(api.membersSkillsImport).toHaveBeenCalledTimes(1);
  expect(api.membersSkillsUpdate).toHaveBeenLastCalledWith(7, 9, false);
  expect(draft.skills).toHaveLength(0);
});

test("an accepted legacy creation never creates twice if member lookup initially fails", async () => {
  const create = vi.fn(async () => true);
  vi.mocked(api.membersList).mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ members: [member], groups: [] });
  vi.mocked(api.membersCommandsSave).mockResolvedValue({});
  const dialog = openMemberProfile(host({ create }));
  fill(dialog.el, '#mb-new-name', 'Mia');
  click(dialog.el, '#mp-tab-skills');
  fill(dialog.el, '#mp-command-name', 'review'); fill(dialog.el, '#mp-command-template', 'Review {input}');
  click(dialog.el, '[data-res="command-save"]'); await flush();
  click(dialog.el, '[data-dlg="create"]'); await flush();
  expect(dialog.el.isConnected).toBe(true);
  click(dialog.el, '[data-dlg="create"]'); await flush();
  expect(create).toHaveBeenCalledTimes(1);
  expect(dialog.el.isConnected).toBe(false);
});

test("a new member routine remains a draft until the member exists", async () => {
  const dialog = openMemberProfile(host());
  fill(dialog.el, '#mb-new-name', 'Mia');
  click(dialog.el, '#mp-tab-routines'); await flush();
  click(dialog.el, '[data-dlg="routine-add"]');
  const form = document.querySelector<HTMLElement>('.au-routine-dialog')!;
  click(form, '[data-kind="daily"]'); fill(form, '#au-time', '09:00');
  fill(form, '#au-title', 'Morning review'); fill(form, '#au-prompt', 'Review the project');
  click(form, '[data-dlg="save"]'); await flush();
  expect(api.routinesCreate).not.toHaveBeenCalled();
  expect(dialog.el.textContent).toContain('Morning review');
  click(dialog.el, '[data-dlg="create"]'); await flush();
  expect(api.routinesCreate).toHaveBeenCalledWith({ member_id: 7, title: 'Morning review', prompt: 'Review the project', enabled: true, schedule: expect.objectContaining({ kind: 'daily', time: '09:00' }) });
});

test("the details panel standalone skills page omits the separate command editor", async () => {
  const container = document.createElement('div');
  const editor = mountMemberResources(container, { memberId: 7, tab: 'skills', standalone: true });
  await flush();
  expect(container.querySelector('#mp-skill-source')).not.toBeNull();
  expect(container.querySelector('#mp-command-template')).toBeNull();
  editor.destroy();
});

test("a successful command mutation uses its result without a second fallible list request", async () => {
  const container = document.createElement('div'); document.body.append(container);
  const editor = mountMemberResources(container, { memberId: 7, tab: 'commands' }); await flush();
  vi.mocked(api.membersCommandsSave).mockResolvedValue({ command: { id: 10, name: 'review', description: '', template: 'Review {input}' } });
  vi.mocked(api.membersCommandsList).mockRejectedValue(new Error('offline'));
  fill(container, '#mp-command-name', '/Review'); fill(container, '#mp-command-template', 'Review {input}');
  click(container, '[data-res="command-save"]'); await flush();
  expect(container.textContent).toContain('/review');
  expect(container.querySelector<HTMLInputElement>('#mp-command-name')!.value).toBe('');
  expect(api.membersCommandsList).toHaveBeenCalledTimes(1);
  editor.destroy();
});

test("a late command save cannot erase the next subpage after the editor is destroyed", async () => {
  let finish!: (value: unknown) => void;
  vi.mocked(api.membersCommandsSave).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  const container = document.createElement('div'); document.body.append(container);
  const editor = mountMemberResources(container, { memberId: 7, tab: 'commands' }); await flush();
  fill(container, '#mp-command-name', 'review'); fill(container, '#mp-command-template', 'Review {input}');
  click(container, '[data-res="command-save"]');
  editor.destroy(); container.innerHTML = '<p>Next member page</p>';
  finish({ command: { id: 10, name: 'review', description: '', template: 'Review {input}' } }); await flush();
  expect(container.innerHTML).toBe('<p>Next member page</p>');
});

test("editing a saved routine from the profile preserves schedule end conditions", async () => {
  const routine = { id: 4, member_id: 7, title: "Review", prompt: "Review updates", enabled: true, last_run: null, next_run: null, schedule: { kind: "weekly", days: [1, 3], time: "08:45", start_date: "2026-10-26", max_runs: 12 } } as const;
  vi.mocked(api.routinesList).mockResolvedValue({ routines: [{ ...routine, schedule: { ...routine.schedule, days: [1, 3] } }] });
  vi.mocked(api.routinesUpdate).mockResolvedValue({ routine: { ...routine, schedule: { ...routine.schedule, days: [1, 3] } } });
  const dialog = openEditMemberDialog(host(), member, "routines"); await flush();
  click(dialog.el, '[data-dlg="routine-edit"]');
  const form = document.querySelector<HTMLElement>(".au-routine-dialog")!;
  expect(form.querySelector<HTMLInputElement>("#au-count")!.value).toBe("12");
  fill(form, "#au-title", "Updated review"); click(form, '[data-dlg="save"]'); await flush();
  expect(api.routinesUpdate).toHaveBeenCalledWith(4, { title: "Updated review", prompt: "Review updates", schedule: routine.schedule });
  expect(form.isConnected).toBe(false); dialog.close();
});

test("a profile routine draft can be edited and deleted without sending a negative member id", async () => {
  const dialog = openMemberProfile(host());
  click(dialog.el, '#mp-tab-routines'); await flush(); click(dialog.el, '[data-dlg="routine-add"]');
  let form = document.querySelector<HTMLElement>(".au-routine-dialog")!;
  fill(form, "#au-title", "Draft review"); fill(form, "#au-prompt", "Review updates");
  click(form, '[data-dlg="save"]'); await flush();
  click(dialog.el, '[data-dlg="routine-edit"]'); form = document.querySelector<HTMLElement>(".au-routine-dialog")!;
  fill(form, "#au-title", "Updated draft"); click(form, '[data-dlg="save"]'); await flush();
  expect(dialog.el.querySelector(".mp-routine-list")!.textContent).toContain("Updated draft");
  click(dialog.el, '[data-dlg="routine-edit"]'); form = document.querySelector<HTMLElement>(".au-routine-dialog")!;
  click(form, '[data-dlg="delete"]'); click(document, '.mb-confirm [data-dlg="delete"]'); await flush();
  expect(dialog.el.querySelector('[data-dlg="routine-edit"]')).toBeNull();
  expect(api.routinesCreate).not.toHaveBeenCalled(); expect(api.routinesUpdate).not.toHaveBeenCalled(); expect(api.routinesDelete).not.toHaveBeenCalled();
  dialog.close();
});
