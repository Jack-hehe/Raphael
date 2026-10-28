export type RunPeriod = "week" | "month" | "last_month" | "all";
export interface RunRequest { range: RunPeriod; tz?: string }
export interface RunSummary { duration_ms: number; cost_usd: number; tokens: number; ai_steps: number; tools: number; errors: number }
export type RunGrouping = "member" | "model" | "day" | "provider" | "routine";
export interface RunGroup { label: string; tokens: number; cost_usd: number; runs: number; member_id?: number; avatar_url?: string | null }
export interface UsageBill { cost_usd: number; purposes: { purpose: string; cost_usd: number; share: number }[] }
export interface RunReport { range: RunPeriod; start_day: string | null; end_day: string; summary: RunSummary; groups: Record<RunGrouping, RunGroup[]>; total: number; bill: UsageBill; fish_voice: boolean }
export interface CliAllowanceWindow {
  period: "fiveHour" | "weekly" | "weeklyOpus" | "weeklySonnet";
  remaining_percent: number | null; resets_at: number | null;
  status?: "allowed" | "allowed_warning" | "rejected"; updated_at_ms?: number;
}
export interface CliAllowances {
  quotas?: { provider: string; reset_at_ms: number | null }[];
  brains: { id: string; name: string; source?: "live" | "reported" | "unavailable"; windows: CliAllowanceWindow[] }[];
}
export interface RunAnalyticsApi {
  modelsCatalog?(): Promise<import("./types").ModelCatalog>;
  brainsCli?(): Promise<{ clis: import("./types").CliBrain[] }>;
  usageRuns(params: RunRequest): Promise<RunReport>;
  usageExport(params: RunRequest): Promise<{ csv: string }>;
  usageAllowances(): Promise<CliAllowances>;
  saveUsageCsv(csv: string): Promise<boolean>;
}
