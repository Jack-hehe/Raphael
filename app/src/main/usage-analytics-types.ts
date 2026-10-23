/** Local-day buckets, ordered oldest first. Shares are fractions of the period total. */
export interface UsageSeries {
  key: string;
  label: string;
  values: number[];
  total: number;
  share: number;
}
export interface UsageGroup { total: number; series: UsageSeries[] }
export interface UsageRequest { range: "7d" | "30d"; end_day?: string; tz?: string }
export interface UsageMember {
  id: number;
  name: string;
  role: string;
  deleted: boolean;
  turns: number;
  cost_usd: number;
  share: number;
  models: { model: string; turns: number; cost_usd: number }[];
}
export interface UsageAnalytics {
  range: UsageRequest["range"];
  start_day: string;
  end_day: string;
  today_day: string;
  days: string[];
  tz: string;
  cost_by: Record<"purpose" | "model" | "provider", UsageGroup>;
  members: UsageMember[];
  tools: UsageGroup;
  messages_by: Record<"model" | "source", UsageGroup>;
  dictation: {
    dictations: number[];
    words: number[];
    estimated: boolean[];
    estimated_words: number;
    estimated_dictations: number;
    day_basis: "capture_local";
  };
}
