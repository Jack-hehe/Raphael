export type RunPeriod = "week" | "month" | "last_month" | "all";
export interface RunRequest { range: RunPeriod; tz?: string; offset?: number; limit?: number }
export interface RunSummary { duration_ms: number; cost_usd: number; tokens: number; ai_steps: number; tools: number; errors: number }
export interface UsageRun extends Omit<RunSummary, "duration_ms" | "tokens"> {
  kind: "turn" | "task" | "routine"; id: number; at_ms: number; started_at_ms: number | null; duration_ms: number | null;
  tokens: number | null; member_id: number; member_name: string; avatar: string; avatar_url?: string | null; title: string;
  model: string; provider: string; routine: string; status?: string;
}
export type RunGrouping = "member" | "model" | "day" | "provider" | "routine";
export interface RunGroup { label: string; tokens: number; cost_usd: number; runs: number; member_id?: number; avatar_url?: string | null }
export interface RunReport { range: RunPeriod; start_day: string | null; end_day: string; summary: RunSummary; groups: Record<RunGrouping, RunGroup[]>; runs: UsageRun[]; total: number }
export interface RunSpan { at_ms: number; ms: number; kind: string; tool: string; server: string; outcome: string; model?: string }
export interface RunTrace { run: UsageRun; summary: RunSummary; spans: RunSpan[] }
export interface CliAllowances { quotas?: { provider: string; reset_at_ms: number | null }[]; brains: { id: string; name: string; windows: { period: "fiveHour" | "weekly"; remaining_percent: number; resets_at: number | null }[] }[] }
export interface RunAnalyticsApi {
  modelsCatalog?(): Promise<import("./types").ModelCatalog>;
  brainsCli?(): Promise<{ clis: import("./types").CliBrain[] }>;
  usageRuns(params: RunRequest): Promise<RunReport>;
  usageTrace(params: { kind: "turn" | "task" | "routine"; id: number }): Promise<RunTrace>;
  usageExport(params: RunRequest): Promise<{ csv: string }>;
  usageAllowances(): Promise<CliAllowances>;
  saveUsageCsv(csv: string): Promise<boolean>;
}
