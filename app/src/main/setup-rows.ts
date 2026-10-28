// The rows that the first-run guide and the settings both show: the key to paste, the monthly limit,
// the microphone. One drawing each; the callers name their own hooks (data-ob in the guide,
// data-act / data-k in the settings) and the text under the row.

import { t } from "./i18n";
import { type Choice, actionRow, btn, fieldHTML, meter, row, sel } from "./ui";

type RowOptions = { cls?: string; attrs?: string };

/** The row that takes a key: the field, 「貼上」, and 「取消」 when there is a key to go back to. */
export function keyEntryRow(o: {
  desc: string;
  fieldWidth: number;
  inputId: string;
  /** The hook of the button called `name` ("key-paste", "key-cancel"), as attributes with a leading space. */
  hook: (name: string) => string;
  busy: boolean;
  invalid: boolean;
  canCancel: boolean;
  row?: RowOptions;
}): string {
  return row(
    t("common.apiKey"),
    o.desc,
    fieldHTML("sk-or-v1-…", "", { w: o.fieldWidth, get label() { return t("common.apiKeyField"); }, id: o.inputId, type: "password", invalid: o.invalid }) +
      actionRow({
        secondary: o.canCancel ? btn(t("common.cancel"), "", `${o.hook("key-cancel")}${o.busy ? " disabled" : ""}`) : "",
        primary: { label: o.busy ? t("common.checking") : t("common.paste"), attrs: `${o.hook("key-paste")}${o.busy ? " disabled" : ""}` },
      }),
    o.row,
  );
}

/** The row for the monthly limit in US$. */
export function limitRow(o: { text: string; fieldWidth: number; id: string; invalid: boolean; disabled?: boolean }): string {
  return row(
    t("common.monthlyLimit"),
    t("common.monthlyLimitDesc"),
    fieldHTML("", o.text, { w: o.fieldWidth, pre: "US$", get label() { return t("common.monthlyLimit"); }, num: true, id: o.id, invalid: o.invalid, disabled: o.disabled }),
  );
}

/** The row for the microphone: the dropdown and the level meter beside it (or, in the settings, under it). */
export function micRow(o: {
  choices: Choice[];
  current: string;
  /** Attributes of the dropdown, with a leading space. */
  selAttrs: string;
  /** "after": the dropdown, then a meter of this many segments, wrapped as one control. "before": the meter, then the dropdown. */
  meter: { place: "before" } | { place: "after"; segments: number };
  row?: RowOptions;
}): string {
  const dropdown = sel(o.choices, o.current, t("common.microphone"), o.selAttrs);
  const ctl = o.meter.place === "before" ? meter() + dropdown : `<span class="mic-ctl">${dropdown}${meter(o.meter.segments)}</span>`;
  return row(t("common.microphone"), "", ctl, o.row);
}
