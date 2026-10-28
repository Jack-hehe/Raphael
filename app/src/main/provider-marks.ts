// Provider kinds and model connection names use the same bundled brand marks as services.
import { iconHTML } from "./connections";
import { LOGO_IDS, type ServiceIconId, type LogoIconId } from "./service-icons";
import { ico } from "./ui";

const MARKS: Record<string, ServiceIconId> = {
  openrouter: "openrouter", openai: "openai", anthropic: "anthropic", google: "googlegemini",
  azure_openai: "azure", bedrock: "aws", litellm: "litellm", portkey: "portkey", nebius: "nebius",
  bifrost: "bifrost", groq: "groq", ollama: "ollama", vercel: "vercel", lmstudio: "lmstudio",
  deepseek: "deepseek", mistral: "mistral", googlegemini: "googlegemini", googlecloud: "googlecloud",
  google_gemini: "googlegemini", google_cloud: "googlecloud", lm_studio: "lmstudio", azure: "azure",
  amazon_bedrock: "aws", ollama_cloud: "ollama", vertex: "vertex",
};

/** The makers of models by the author of a model's id ("qwen/qwen3-…", "~openai/…"), where it is not the mark's own id. */
const MAKERS: Record<string, ServiceIconId> = {
  google: "googlegemini", mistralai: "mistral", "z-ai": "zai", moonshotai: "moonshot", "meta-llama": "meta",
  "x-ai": "xai", "inference-net": "inference_net", "arcee-ai": "arcee", "aion-labs": "aionlabs", "bytedance-seed": "bytedance",
  "ibm-granite": "ibm", rekaai: "reka", "dots-studio": "dotsstudio", cognitivecomputations: "dolphin", "nex-agi": "nex_agi",
  "xingchen-agi": "xingchen_agi", "prism-ml": "prism_ml", "anthracite-org": "anthracite", "black-forest-labs": "bfl",
  kwaivgi: "kling", voyageai: "voyage", "nace-ai": "nace_ai", "sentence-transformers": "sentence_transformers",
  "fish-audio": "fish", togethercomputer: "together",
};

const markOf = (key: string): ServiceIconId | null =>
  MARKS[key.replace(/-/g, "_")] ?? MAKERS[key] ?? (LOGO_IDS.includes(key.replace(/-/g, "_") as LogoIconId) ? key.replace(/-/g, "_") as LogoIconId : null);

/**
 * A model's mark: its maker's, found by the author in its id or by the maker's name. A maker with no
 * artwork of its own gets a neutral model mark, so that every row of a list lines up.
 */
export function modelMark(model: { id: string; provider?: string }): string {
  const author = model.id.includes("/") ? model.id.split("/", 1)[0].replace(/^~/, "").toLowerCase() : "";
  const maker = (model.provider ?? "").toLowerCase().replace(/[\s.]+/g, "");
  const id = (author && markOf(author)) || (maker && markOf(maker));
  return id ? iconHTML(model.provider ?? author, id, "md") : `<span class="mono md" aria-hidden="true">${ico("bot")}</span>`;
}

/** An unrecognised custom connection, or artwork not installed yet, gets a plug. */
export function providerMark(kind: string, officialOnly = false): string {
  const key = kind.toLowerCase().replace(/[\s-]+/g, "_").replace(/_cn$/, "");
  const id = MARKS[key] ?? (LOGO_IDS.includes(key as LogoIconId) ? key as LogoIconId : null);
  return officialOnly && !id ? "" : iconHTML(kind, id ?? null, "md");
}
