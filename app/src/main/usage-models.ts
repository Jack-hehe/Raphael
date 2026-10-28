/** Model labels shared by retained runs and daily usage charts. */
import { t } from "./i18n";
import { modelMark } from "./provider-marks";
import type { CatalogModel } from "./types";
import { esc } from "./ui";

export function usageModel(id: string, models: readonly CatalogModel[] = []): { name: string; html: string } {
  if (!id || id === "unknown") return {name:t("runs.unknown"),html:esc(t("runs.unknown"))};
  const raw = id.replace(/^@[^:]+:/, "").replace(/^cli:[^:]+:/, "");
  const model = models.find(model => model.id === id || model.id === raw);
  const provider = model?.provider ?? (id.startsWith("cli:codex") || raw.startsWith("openai/") ? "OpenAI" : id.startsWith("cli:claude") ? "Anthropic" : id.startsWith("cli:gemini") ? "Google" : undefined);
  const name = model?.name ?? raw.split("/").pop()!.replace(/[_-]+/g, " ").replace(/\bgpt\b/gi, "GPT");
  return {name,html:`<span class="run-model">${modelMark({id:raw,provider})}<span>${esc(name)}</span></span>`};
}
