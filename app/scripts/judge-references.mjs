import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve, join } from "node:path";
import { pathToFileURL } from "node:url";

export function parseManifest(text) {
  const manifest = JSON.parse(text || "[]");
  if (!Array.isArray(manifest) || !manifest.length || manifest.length > 6) throw new Error("Configure RAPHAEL_JUDGE_REFERENCE_MANIFEST with 1-6 public HTTPS image URLs and pinned sha256 hashes; reference art stays outside the repository.");
  for (const item of manifest) {
    const url = new URL(item.url);
    if (url.protocol !== "https:" || url.username || url.password || !/^[a-f0-9]{64}$/.test(item.sha256)) throw new Error("Invalid pinned reference manifest");
  }
  return manifest;
}

export async function fetchReferences(manifest, output, fetchImage = fetch) {
  await mkdir(output, { recursive: true });
  const paths = [];
  for (const [index, item] of manifest.entries()) {
    const response = await fetchImage(item.url, { redirect: "error", signal: AbortSignal.timeout(30000) });
    if (!response.ok || Number(response.headers.get("content-length")) > 10 * 1024 * 1024) throw new Error("Reference download failed or exceeds 10 MiB");
    const chunks = [];
    let bytes = 0;
    for await (const chunk of response.body) {
      bytes += chunk.length;
      if (bytes > 10 * 1024 * 1024) throw new Error("Reference exceeds 10 MiB");
      chunks.push(chunk);
    }
    const data = Buffer.concat(chunks);
    const png = data.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
    const jpeg = data[0] === 255 && data[1] === 216;
    if ((!png && !jpeg) || createHash("sha256").update(data).digest("hex") !== item.sha256) throw new Error("Reference image hash or format mismatch");
    const path = join(output, `${index}.${png ? "png" : "jpg"}`);
    await writeFile(path, data);
    paths.push(path);
  }
  await writeFile(join(output, "paths.json"), JSON.stringify(paths) + "\n");
  return paths;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    await fetchReferences(parseManifest(process.env.RAPHAEL_JUDGE_REFERENCE_MANIFEST), resolve(".look/references"));
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
