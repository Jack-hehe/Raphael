// @vitest-environment happy-dom
import { beforeEach, expect, test, vi } from "vitest";
import { membersCommandsList } from "./api";
import { matchingCommands, mountSlashMenu } from "./slash-menu";
import { closeMenu } from "./ui";
vi.mock("./api", () => ({ membersCommandsList: vi.fn() }));
const commands = [{ id: 1, name: 'review', description: 'Review code', template: 'Secret expansion {input}' }, { id: 2, name: 'research', description: 'Find sources', template: '{input}' }];
const flush = async (): Promise<void> => { for (let i = 0; i < 15; i++) await Promise.resolve(); };
beforeEach(() => { closeMenu(); document.body.innerHTML = ''; vi.resetAllMocks(); vi.mocked(membersCommandsList).mockResolvedValue({ commands }); });
const setup = () => { const input = document.createElement('textarea'); document.body.append(input); input.focus(); let id = 7; const menu = mountSlashMenu(input, { memberId: () => id }); return { input, menu, selectMember: (next: number) => { id = next; } }; };
const type = (input: HTMLTextAreaElement, value: string) => { input.value = value; input.dispatchEvent(new Event('input', { bubbles: true })); };

test('command filtering is limited to a slash at the start and excludes arguments', () => {
  expect(matchingCommands(commands, '/REv').map(c => c.name)).toEqual(['review']);
  for (const text of ['hello /', '/review text', ' /']) expect(matchingCommands(commands, text)).toEqual([]);
});

test('typing slash fetches that member commands and mouse selection preserves slash text', async () => {
  const { input, menu } = setup(); type(input, '/'); await flush();
  expect(membersCommandsList).toHaveBeenCalledWith(7);
  document.querySelector<HTMLElement>('[data-slash="0"]')!.click();
  expect(input.value).toBe('/review ');
  expect(input.value).not.toContain('Secret');
  expect(document.querySelector('#app-popover')).toBeNull();
  menu.destroy();
});

test('arrow and Enter select without letting the composer send; IME Enter remains untouched', async () => {
  const { input, menu } = setup(); type(input, '/'); await flush();
  const send = vi.fn(); input.addEventListener('keydown', send);
  input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true }));
  expect(input.value).toBe('/'); expect(send).toHaveBeenCalledTimes(1);
  input.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true }));
  input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
  expect(input.value).toBe('/research '); expect(send).toHaveBeenCalledTimes(1);
  menu.destroy();
});

test('stale command responses cannot appear in a different member conversation', async () => {
  let finish!: (answer: { commands: typeof commands }) => void;
  vi.mocked(membersCommandsList).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  const { input, menu, selectMember } = setup(); type(input, '/'); selectMember(8);
  finish({ commands }); await flush();
  expect(document.querySelector('#app-popover')).toBeNull(); menu.destroy();
});

test('Escape dismisses, arguments close, and destroy removes every input hook', async () => {
  const { input, menu } = setup(); type(input, '/'); await flush();
  input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  expect(document.querySelector('#app-popover')).toBeNull();
  type(input, '/review something'); expect(document.querySelector('#app-popover')).toBeNull();
  menu.destroy(); type(input, '/'); expect(document.querySelector('#app-popover')).toBeNull();
});

test('leaving the composer closes its menu and invalidates a pending refresh', async () => {
  const { input, menu } = setup(); type(input, '/'); await flush();
  input.dispatchEvent(new Event('blur'));
  expect(document.querySelector('#app-popover')).toBeNull();
  let finish!: (answer: { commands: typeof commands }) => void;
  vi.mocked(membersCommandsList).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  const pending = menu.refresh();
  input.dispatchEvent(new Event('blur'));
  finish({ commands }); await pending;
  expect(document.querySelector('#app-popover')).toBeNull();
  menu.destroy();
});

test('menu placement is recalculated after applying the composer width on a narrow viewport', async () => {
  const width = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetWidth');
  const beforeViewport = innerWidth;
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 720 });
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get() { return Number.parseFloat((this as HTMLElement).style.minWidth) || 0; } });
  const { input, menu } = setup();
  vi.spyOn(input, 'getBoundingClientRect').mockReturnValue({ x: 220, y: 800, top: 800, bottom: 840, left: 220, right: 700, width: 480, height: 40, toJSON() {} });
  try {
    type(input, '/'); await flush();
    const panel = document.querySelector<HTMLElement>('.slash-menu')!;
    expect(panel.style.minWidth).toBe('480px');
    expect(Number.parseFloat(panel.style.left) + 480).toBeLessThanOrEqual(712);
  } finally {
    menu.destroy();
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: beforeViewport });
    if (width) Object.defineProperty(HTMLElement.prototype, 'offsetWidth', width);
    else delete (HTMLElement.prototype as unknown as Record<string, unknown>).offsetWidth;
  }
});
