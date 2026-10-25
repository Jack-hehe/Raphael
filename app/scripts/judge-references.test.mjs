import { createHash } from "node:crypto";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it } from "vitest";
import { fetchReferences, parseManifest } from "./judge-references.mjs";

it("requires pinned HTTPS references without machine-specific paths", () => {
  for (const text of ["[]", '[{"url":"file:///private/reference.png","sha256":"x"}]', '[{"url":"https://user:password@example.com/image.png","sha256":"x"}]']) expect(() => parseManifest(text)).toThrow();
  expect(parseManifest(JSON.stringify([{ url: "https://example.com/reference.png", sha256: "a".repeat(64) }]))).toHaveLength(1);
});
it("checks reference bytes offline before accepting them", async () => {
  const output = await mkdtemp(join(tmpdir(), "guardian-references-"));
  const data = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  const item = { url: "https://example.com/reference.png", sha256: createHash("sha256").update(data).digest("hex") };
  const fakeFetch = async () => new Response(data);
  try {
    expect(await fetchReferences([item], output, fakeFetch)).toHaveLength(1);
    await expect(fetchReferences([{ ...item, sha256: "0".repeat(64) }], output, fakeFetch)).rejects.toThrow(/hash/);
  } finally { await rm(output, { recursive: true, force: true }); }
});
