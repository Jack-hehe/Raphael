// Calendar math uses local calendar days, including the PC's time zone offset at each occurrence.
import type { Routine } from "./types";
import { dateKey, localDay, shiftDay } from "./schedule-picker";
export interface Occurrence { routine: Routine; at: number; }
export function occurrences(routine: Routine, from: Date, to: Date, remaining = Infinity): Occurrence[] {
  if (!routine.enabled || routine.next_run === null || remaining <= 0) return [];
  const s = routine.schedule, result: Occurrence[] = [];
  let used = 0;
  const add = (at: number): void => {
    const day = dateKey(new Date(at));
    if ((!s.start_date || day >= s.start_date) && (!s.until || day <= s.until) && used < remaining) {
      used++;
      if (at >= from.getTime() && at < to.getTime()) result.push({ routine, at });
    }
  };
  if (s.kind === "once") { add(new Date(`${s.start_date}T${s.time}`).getTime()); return result; }
  if (s.kind === "every") {
    const step = s.hours * 3600000, anchor = routine.next_run;
    const offset = Math.max(0, Math.ceil((from.getTime() - anchor) / step));
    used = offset;
    for (let at = anchor + offset * step; at < to.getTime() && used < remaining; at += step) add(at);
    return result;
  }
  const first = Number.isFinite(remaining) ? new Date(routine.next_run) : from;
  for (let day = localDay(dateKey(first)); day < to && used < remaining; day = shiftDay(day, 1)) {
    if (s.kind === "weekdays" && [0, 6].includes(day.getDay())) continue;
    if (s.kind === "weekly" && !s.days.includes(day.getDay())) continue;
    const at = new Date(`${dateKey(day)}T${s.time}`).getTime();
    if (at >= routine.next_run) add(at);
  }
  return result;
}
