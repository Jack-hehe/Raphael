import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";

// Paginate large matrices so each sheet remains readable at full resolution.
export async function contactSheets(browser, results, directory) {
  const files = [];
  for (let offset = 0; offset < results.length; offset += 16) {
    const entries = results.slice(offset, offset + 16);
    const images = await Promise.all(entries.map(async entry => ({ name: `${entry.scene} · ${entry.theme} · ${entry.width}`, failed: entry.failed, image: entry.image ? (await readFile(join(directory, entry.image))).toString("base64") : null })));
    const page = await browser.newPage();
    try {
      const rows = Math.ceil(entries.length / 4);
      await page.setViewport({ width: 1680, height: rows * 380, deviceScaleFactor: 1 });
      await page.setContent('<!doctype html><html><head><meta charset="UTF-8"><style>body{margin:0;background:#e5e5e5;color:#111;font:14px sans-serif}main{display:grid;grid-template-columns:repeat(4,420px)}figure{margin:0;padding:10px;box-sizing:border-box;height:380px}figcaption{height:30px;overflow:hidden}img{width:400px;height:330px;object-fit:contain;object-position:top;background:white}.failed{color:#a01818;font-weight:bold}</style></head><body><main></main></body></html>');
      await page.evaluate(items => {
        for (const item of items) {
          const figure = document.createElement("figure"), caption = document.createElement("figcaption");
          caption.textContent = `${item.failed ? "FAIL · " : ""}${item.name}`;
          if (item.failed) caption.className = "failed";
          figure.append(caption);
          if (item.image) { const image = document.createElement("img"); image.src = `data:image/png;base64,${item.image}`; figure.append(image); }
          else figure.append("Capture unavailable; see report.json");
          document.querySelector("main").append(figure);
        }
      }, images);
      await page.evaluate(async () => { await document.fonts.ready; await Promise.all([...document.images].map(image => image.decode())); });
      const file = `contact-${String(files.length + 1).padStart(2, "0")}.png`;
      await page.screenshot({ path: join(directory, file), type: "png" });
      files.push(file);
    } finally { await page.close(); }
  }
  await writeFile(join(directory, "contacts.json"), JSON.stringify(files, null, 2) + "\n");
  return files;
}
