// Provider kinds and model connection names use the same bundled brand marks as services.
import { iconHTML } from "./connections";
import type { ServiceIconId } from "./service-icons";

const MARKS: Record<string, ServiceIconId> = {
  openrouter: "openrouter", openai: "openai", anthropic: "anthropic", google: "googlegemini",
  azure_openai: "azure", bedrock: "aws", litellm: "litellm", portkey: "portkey", nebius: "nebius",
  bifrost: "bifrost", groq: "groq", ollama: "ollama", vercel: "vercel", lmstudio: "lmstudio",
  deepseek: "deepseek", mistral: "mistral", googlegemini: "googlegemini", googlecloud: "googlecloud",
  google_gemini: "googlegemini", google_cloud: "googlecloud", lm_studio: "lmstudio", azure: "azure",
  amazon_bedrock: "aws",
};

/** An unrecognised custom connection, or artwork not installed yet, gets a plug. */
export function providerMark(kind: string): string {
  const id = MARKS[kind.toLowerCase().replace(/[\s-]+/g, "_")];
  return iconHTML(kind, id ?? null, "md");
}
