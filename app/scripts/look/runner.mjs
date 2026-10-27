import { existsSync } from "node:fs";
import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { resolve, join } from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { installMock } from "./mock-ipc.js";
import { auditDocument, failedChecks } from "./audit.mjs";
import { perform } from "./driver.mjs";
import { contactSheets } from "./contact-sheet.mjs";
import { requestDisposition } from "./requests.mjs";

/** Product, model, unit, key names and external menu paths that stay in Latin letters in every language. */
export const LATIN_NAMES = ["Raphael", "Rimuru", "Google", "Gmail", "Calendar", "Composio", "OpenRouter", "OpenAI", "Anthropic", "Claude", "Codex", "Gemini", "GPT", "Grok", "Telegram", "Slack", "Discord", "Notion", "GitHub", "MCP", "API", "URL", "CLI", "STT", "PDF", "CSV", "JSON", "US", "KB", "MB", "GB", "GMT", "AM", "PM", "Alt", "AltGr", "Ctrl", "Shift", "Space", "Esc", "Enter", "Tab", "Win", "Windows", "Fn", "Edge", "Chrome", "macOS", "Whisper", "Typeless", "Webhook", "Hermes", "Simple", "Icons", "Bearer", "mcpServers", "Ollama", "Linear", "BotFather", "newbot", "English", "MIT", "JPG", "WebP", "GIF", "UTF", "Big", "txt", "px", "token", "Claude Code", "Antigravity", "Catppuccin", "Latte", "Mocha", "KEY=value", "Settings → Project Settings → API Keys"];

// Agents run inside their own sandbox, where the browser cannot start a second one; the page is our
// own app on loopback only.
const BROWSER_ARGS = ["--no-sandbox", "--disable-gpu", "--disable-crash-reporter", "--no-first-run", "--no-default-browser-check", "--disable-background-networking", "--disable-component-update"];

const app = resolve(import.meta.dirname, "../..");
const requireFromApp = createRequire(join(app, "package.json"));
export const REQUIRED_PUPPETEER = "puppeteer-core@25.13.0";

function browserPath() {
  const candidates = [process.env.LOOK_BROWSER,
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "/usr/bin/microsoft-edge", "/usr/bin/google-chrome", "/usr/bin/chromium"];
  const found = candidates.find(path => path && existsSync(path));
  if (!found) throw new Error("Installed Edge/Chrome not found; set LOOK_BROWSER to its executable path.");
  return found;
}

export async function run(options) {
  await mkdir(options.out, { recursive: true });
  // Keep each invocation separate: a blocked run must never inherit old green screenshots.
  const directory = await mkdtemp(join(options.out, "run-"));
  const report = { status: "running", startedAt: new Date().toISOString(), options: { scenes: options.selected.map(scene => scene.name), themes: options.themes, widths: options.widths, heights: options.heights, skin: options.skin, locale: options.locale }, results: [], contactSheets: [], errors: [] };
  let server, browser;
  const save = () => writeFile(join(directory, "report.json"), JSON.stringify(report, null, 2) + "\n");
  try {
    let puppeteer;
    try { puppeteer = (await import(pathToFileURL(requireFromApp.resolve("puppeteer-core")).href)).default; }
    catch (error) {
      if (error.code !== "MODULE_NOT_FOUND" && error.code !== "ERR_MODULE_NOT_FOUND") throw error;
      report.status = "blocked";
      throw new Error(`Missing ${REQUIRED_PUPPETEER} in app/node_modules. Offline browser verification stopped. Provide that devDependency before retrying; no download was attempted.`);
    }
    const executablePath = browserPath();
    report.executablePath = executablePath;
    const { createServer } = await import(pathToFileURL(requireFromApp.resolve("vite")).href);
    const fixture = JSON.parse(await readFile(join(import.meta.dirname, "fixture.json"), "utf8"));
    const translations = await readFile(join(app, "src/main/i18n/zh-TW.ts"), "utf8");
    // Words a Chinese window may show in Latin letters: the synthetic data's own, and names of products and keys.
    const dataWords = value => typeof value === "string" ? value.match(/[A-Za-z]{2,}/g) ?? [] : Array.isArray(value) ? value.flatMap(dataWords) : value && typeof value === "object" ? Object.values(value).flatMap(dataWords) : [];
    const latinFor = scene => options.locale === "zh-TW" ? [...new Set([...dataWords(fixture), ...dataWords(scene.fixture), ...LATIN_NAMES].map(word => word.toLowerCase()))] : null;
    const namespaces = [...new Set([...translations.matchAll(/"([a-z][A-Za-z0-9]*)\.[^"\n]+"\s*:/g)].map(match => match[1]))];
    server = await createServer({ configFile: false, root: app, cacheDir: join(directory, "vite-cache"), server: { host: "127.0.0.1", port: 0, hmr: false, watch: null, fs: { allow: [app] } } });
    await server.listen();
    const address = server.httpServer.address();
    const origin = `http://127.0.0.1:${address.port}`;
    browser = await puppeteer.launch({ executablePath, headless: true, userDataDir: join(directory, "browser-profile"), args: BROWSER_ARGS, timeout: 60000 });
    report.browser = await browser.version();
    for (const scene of options.selected) for (const theme of options.themes) for (const width of options.widths) {
      const page = await browser.newPage();
      const runtimeErrors = [];
      const result = { scene: scene.name, theme, width, height: options.heights[width] ?? 900, skin: options.skin, locale: options.locale, runtimeErrors, image: null, failed: true };
      report.results.push(result);
      const error = (kind, message) => runtimeErrors.push({ kind, message });
      page.on("pageerror", value => error("exception", value.message));
      const favicon = url => url && requestDisposition(url, origin) === "empty";
      page.on("console", message => { if (message.type() === "error" && !favicon(message.location().url)) error("console", message.text()); });
      page.on("requestfailed", request => { if (!favicon(request.url())) error("request", `${request.url()}: ${request.failure()?.errorText}`); });
      page.on("response", response => { if (response.status() >= 400 && !favicon(response.url())) error("http", `${response.status()} ${response.url()}`); });
      try {
        await page.setViewport({ width, height: options.heights[width] ?? 900, deviceScaleFactor: 1 });
        await page.emulateTimezone("Asia/Taipei");
        // Still frames by default; a scene about something that moves turns motion on and holds it with `freeze-motion`.
        await page.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: scene.motion ? "no-preference" : "reduce" }, { name: "prefers-color-scheme", value: theme }]);
        await page.setRequestInterception(true);
        page.on("request", request => {
          const url = request.url();
          const disposition = requestDisposition(url, origin);
          if (disposition === "empty") void request.respond({ status: 204 }).catch(() => {});
          else if (disposition === "continue") void request.continue().catch(() => {});
          else { error("network", `External request blocked: ${url}`); void request.abort().catch(() => {}); }
        });
        await page.evaluateOnNewDocument(installMock, fixture, { fixture: scene.fixture, theme, locale: options.locale, skin: options.skin });
        await page.evaluateOnNewDocument(now => {
          const NativeDate = Date;
          window.Date = class extends NativeDate {
            constructor(...args) { super(...(args.length ? args : [now])); }
            static now() { return window.__look.now ?? now; }
          };
          window.addEventListener("unhandledrejection", event => { window.__look.errors.push(`Unhandled rejection: ${String(event.reason)}`); });
        }, fixture.now);
        // One browser profile serves the whole run: a scene starts from the storage its own steps make, never from
        // what an earlier scene left there (a collapsed member column hid the rooms of the scene after it).
        const storage = await page.createCDPSession();
        await storage.send("Storage.clearDataForOrigin", { origin, storageTypes: "local_storage,session_storage" });
        await storage.detach();
        await page.goto(`${origin}/${scene.entry ?? ""}`, { waitUntil: "networkidle0", timeout: 20000 });
        await page.waitForSelector(scene.ready ?? (scene.entry === "overlay.html" ? "#progress:not([hidden])" : scene.fixture.onboarding ? '#ob:not([hidden]) [data-ob="next"]' : '.page-home:not([hidden]) .ho-hero'), { visible: true, timeout: 10000 });
        // Existing conversation journeys enter through the real rail after the home is ready.
        if (scene.entry !== "overlay.html" && !scene.fixture.onboarding && !scene.name.startsWith("home")) {
          await perform(page, { action: "click", selector: '#nav .nav[data-go="agent"]' });
          await page.waitForSelector('.page-agent:not([hidden]) .conversation-trigger', { visible: true, timeout: 10000 });
        }
        for (const step of scene.steps) {
          const assertion = await perform(page, step);
          if (assertion?.check) (result.assertions ??= []).push(assertion);
        }
        await page.evaluate(async () => { await document.fonts.ready; await Promise.all([...document.images].map(image => image.decode().catch(() => {}))); });
        await new Promise(resolve => setTimeout(resolve, 300));
      } catch (value) {
        error("scene", value.message);
        result.diagnostics = await page.evaluate(() => ({ messages: [...document.querySelectorAll('.message-text, .ans, .ra')].map(el => ({ tag: el.tagName, class: el.className, parent: el.parentElement?.outerHTML.slice(0, 500) })) })).catch(() => null);
      }
      try {
        const englishUI = options.locale === "zh-TW" && !scene.name.startsWith("overlay-") ? await page.evaluate(async () => {
          const [{ en }, { zhTW }] = await Promise.all([import('/src/main/i18n/en.ts'), import('/src/main/i18n/zh-TW.ts')]);
          // Brand/key names and deliberately shared CTA decisions have identical values.
          return [...new Set(Object.entries(en).filter(([key, value]) => typeof value === 'string' && /[A-Za-z]/.test(value) && !/[<{}]/.test(value) && /[\u3400-\u9fff]/.test(zhTW[key] ?? '') && value !== zhTW[key]).map(([, value]) => value.trim()))];
        }) : [];
        // The ⋯ buttons that no row of the app's own table (context-menu.ts) reaches with a right click: R-UI-44.
        const unreached = scene.entry === "overlay.html" ? [] : await page.evaluate(async () => {
          const { uncoveredOverflows } = await import('/src/main/context-menu.ts');
          return uncoveredOverflows(document).map(button => ({ tag: "button", id: button.id, class: button.className, text: button.getAttribute("aria-label") ?? "" }));
        });
        Object.assign(result, await page.evaluate(auditDocument, undefined, { width, height: options.heights[width] ?? 900 }, namespaces, latinFor(scene), englishUI, unreached));
        const mockErrors = await page.evaluate(() => window.__look?.errors ?? ["Mock boundary was not installed"]);
        for (const message of mockErrors) error("ipc", message);
        const image = `${scene.name}-${theme}-${width}.png`;
        await page.screenshot({ path: join(directory, image), type: "png" });
        result.image = image;
        result.failed = failedChecks(result);
      } catch (value) { error("capture", value.message); }
      finally { await page.close(); }
      console.log(`${result.failed ? "FAIL" : "PASS"} ${scene.name} ${theme} ${width}`);
      await save();
    }
    report.contactSheets = await contactSheets(browser, report.results, directory);
    report.status = report.results.some(result => result.failed) ? "failed" : "passed";
  } catch (value) {
    if (report.status !== "blocked") report.status = "failed";
    report.errors.push(value.message);
    console.error(value.message);
  } finally {
    try { await browser?.close(); } catch (error) { report.status = "failed"; report.errors.push(`Browser cleanup: ${error.message}`); }
    try { await server?.close(); } catch (error) { report.status = "failed"; report.errors.push(`Vite cleanup: ${error.message}`); }
    report.finishedAt = new Date().toISOString();
    await save();
    console.log(`Report: ${join(directory, "report.json")}`);
  }
  return report.status === "passed" ? 0 : 1;
}
