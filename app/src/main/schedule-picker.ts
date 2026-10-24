// Shared local-date picker. Its buttons and dismissal layer follow the app's menu conventions.
import { getLocale, t } from "./i18n";
import { dayName } from "./members";
import { btn, closeMenu, esc, iconButton, openPopover, placeMenu } from "./ui";

export const dateKey = (day: Date): string => `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
export const localDay = (key: string): Date => new Date(`${key}T00:00:00`);
export const shiftDay = (date: Date, days: number): Date => new Date(date.getFullYear(), date.getMonth(), date.getDate() + days);
export const monthLabel = (day: Date): string => day.toLocaleDateString(getLocale(), { year: "numeric", month: "long" });

export function monthGrid(month: Date, selected: string, attr: string): string {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const start = shiftDay(first, -((first.getDay() + 6) % 7));
  return `<div class="au-month-grid">${[1, 2, 3, 4, 5, 6, 0].map(n => `<span>${esc(dayName(n))}</span>`).join("")}` +
    Array.from({ length: 42 }, (_, i) => {
      const day = shiftDay(start, i), key = dateKey(day);
      return btn(String(day.getDate()), `sm${day.getMonth() === month.getMonth() ? "" : " au-other-month"}`, ` ${attr}="${key}" aria-label="${key}" aria-pressed="${key === selected}"`);
    }).join("") + `</div>`;
}

export function openDatePicker(anchor: HTMLElement, value: string, onPick: (key: string) => void): void {
  let month = localDay(value || dateKey(new Date()));
  if (Number.isNaN(month.getTime())) month = new Date();
  const pop = openPopover(anchor, "", { className: "menu au-date-menu", role: "dialog", label: t("auto.date") });
  const draw = (): void => {
    pop.innerHTML = `<div class="au-month-head">${iconButton("left", t("auto.monthPrevious"), ' data-month="-1"')}<b>${esc(monthLabel(month))}</b>${iconButton("right", t("auto.monthNext"), ' data-month="1"')}</div>` + monthGrid(month, value, "data-date");
    placeMenu();
  };
  pop.addEventListener("click", e => {
    const target = (e.target as Element).closest<HTMLElement>("[data-month], [data-date]");
    if (!target) return;
    if (target.dataset.date) { onPick(target.dataset.date); closeMenu(); anchor.focus(); }
    else { month = new Date(month.getFullYear(), month.getMonth() + Number(target.dataset.month), 1); draw(); }
  });
  draw();
}
