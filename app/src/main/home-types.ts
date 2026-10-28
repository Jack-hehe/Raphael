import type { Status } from "./types";
import type { CliAllowances } from "./run-analytics-types";

export interface HomeItem {
  id: string;
  kind: "question" | "report" | "routine" | "event";
  title: string;
  summary: string;
  at_ms: number;
  member_id: number;
  name: string;
  avatar_url: string | null;
  turn_id: number | null;
  conversation_id: number | null;
  routine_id: number | null;
}
export interface HomeOverview {
  generated_at_ms: number;
  report: Status;
  today: HomeItem[];
  reports: HomeItem[];
  calendar: HomeItem[];
  counts: { today: number; reports: number; calendar: number };
  usage: { month_usd: number; limit_usd: number; daily: number[]; allowances: CliAllowances };
  dictation: { words: number; time_saved_seconds: number; speed_wpm: number | null; audio_seconds: number; daily: number[] };
}
