import type { ConnectionGroup, ConnectionTool, StoreService } from "./types";
import { t } from "./i18n";

export interface ManagedConnector {
  id: string;
  kind: "composio" | "google";
  name?: string;
  description?: string;
  logo?: string;
  status: string;
  enabled: boolean;
  enabled_tools: string[];
  disabled_tools: string[];
  /** Each account the service is signed in with: its status only. */
  accounts?: { id?: string; status: string }[];
}

export interface ConnectorStatus {
  connectors: ManagedConnector[];
  composio_configured: boolean;
  google_configured: boolean;
}

export interface Toolkit {
  slug: string;
  name: string;
  description: string;
  logo: string;
  categories: Array<{ id?: string; slug?: string; name: string } | string>;
  can_connect: boolean;
  no_auth: boolean;
}

export interface ConnectorTool {
  slug: string;
  name: string;
  description: string;
  enabled: boolean;
  kind: "read" | "write";
}

/** Remote artwork is restricted independently of the app's broader media CSP. */
export function pluginLogoURL(value: string | undefined): string | null {
  try {
    const url = new URL(value ?? "");
    return url.protocol === "https:" && /^(?:[a-z0-9-]+\.)*composio\.dev$/.test(url.hostname) &&
      !url.username && !url.password && !url.hash && (!url.port || url.port === "443") ? url.href : null;
  } catch { return null; }
}

export function managedGroup(c: ManagedConnector, previous?: ConnectionGroup): ConnectionGroup {
  const slug = c.id.replace(/^composio:/, "");
  const tools = previous?.tools ?? [];
  return {
    id: c.id, source: c.kind, name: c.kind === "google" ? t("plugins.privateGoogle") : c.name || slug,
    description: c.kind === "google" ? t("plugins.googleDescription") : c.description || "",
    logo: c.logo, enabled: c.enabled !== false, status: c.status === "ACTIVE" ? "ready" : "error",
    error: c.status === "ACTIVE" ? null : "auth", auth: "oauth", catalog_id: c.kind === "composio" ? slug : null,
    accounts: (c.accounts ?? []).map(({ status }) => ({ status })),
    disabled_tools: c.disabled_tools, tools_state: previous?.tools_state,
    tools, tool_count: tools.length || c.enabled_tools.length + c.disabled_tools.length,
    enabled_count: c.enabled_tools.length, write_count: tools.filter(t => t.kind === "write").length,
    read_count: tools.filter(t => t.kind === "read").length, transport: "http", command: "", url: "",
    args: [], env: [], headers: [], checked_at_ms: null,
  };
}

export const pluginTool = (tool: ConnectorTool): ConnectionTool => ({
  name: tool.slug, title: tool.name, description: tool.description, enabled: tool.enabled, kind: tool.kind,
});

export function storePlugin(item: Toolkit, connectors: ManagedConnector[]): StoreService {
  const summary = { "zh-TW": item.description, en: item.description, ja: item.description };
  return {
    id: item.slug, source: "composio", name: item.name, logo: item.logo, icon: null,
    publisher: "Composio", url: "", category: "", popular: false, can_connect: item.can_connect,
    auth: item.no_auth ? "none" : "oauth", summary, about: summary,
    examples: { "zh-TW": [], en: [], ja: [] },
    server_id: connectors.find(c => c.id === `composio:${item.slug}` && c.status === "ACTIVE")?.id ?? null,
  };
}
