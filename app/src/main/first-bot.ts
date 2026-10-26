import { t } from "./i18n";
import { actionRow, btn, esc } from "./ui";

/** Conversation choices collect data; the ordinary admin proposal still requires confirmation. */
export function createFirstBot(onPropose: (description: string) => void) {
  const el = document.createElement("section");
  el.className = "first-bot";
  el.hidden = true;
  let step = 0;
  let dismissed = false;
  const answers = ["", "", ""];
  const titles = ["firstBot.name", "firstBot.personality", "firstBot.work"] as const;
  const options = [
    ["firstBot.nameOne", "firstBot.nameTwo"],
    ["firstBot.calm", "firstBot.warm"],
    ["firstBot.plan", "firstBot.research"],
  ] as const;
  const limits = [16, 2000, 2000];
  function draw() {
    el.innerHTML = `<h2>${esc(t(titles[step]!))}</h2>` +
      `<div class="first-bot-choices">${options[step]!.map((key, index) => btn(esc(t(key)), "first-bot-choice", ` data-first-choice="${index}"`)).join("")}</div>` +
      `<textarea class="mb-fin mb-ta" data-first-answer rows="2" maxlength="${limits[step]}" aria-label="${esc(t(titles[step]!))}" placeholder="${esc(t("firstBot.custom"))}">${esc(answers[step]!)}</textarea>` +
      actionRow({
        secondary: (step > 0 ? btn(esc(t("firstBot.back")), "", ' data-first-act="back"') : "") + btn(esc(t("common.cancel")), "", ' data-first-act="cancel"'),
        primary: { label: esc(t(step === 2 ? "firstBot.review" : "firstBot.next")), attrs: ` data-first-act="next"${answers[step]!.trim() ? "" : " disabled"}` },
      });
  }
  el.addEventListener("input", () => {
    answers[step] = el.querySelector<HTMLTextAreaElement>("textarea")!.value;
    el.querySelector<HTMLButtonElement>('[data-first-act="next"]')!.disabled = !answers[step]!.trim();
  });
  el.addEventListener("click", event => {
    const target = (event.target as Element).closest<HTMLElement>("[data-first-act], [data-first-choice]");
    if (!target || target.hasAttribute("disabled")) return;
    if (target.dataset.firstChoice !== undefined) {
      answers[step] = t(options[step]![Number(target.dataset.firstChoice)]!);
      draw();
      return;
    }
    switch (target.dataset.firstAct) {
      case "cancel": dismissed = true; el.hidden = true; el.dispatchEvent(new Event("change")); return;
      case "back": step--; draw(); return;
      case "next":
        if (!answers[step]!.trim()) return;
        if (step < 2) { step++; draw(); return; }
        dismissed = true;
        el.hidden = true;
        onPropose(t("firstBot.proposal", { name: answers[0]!.trim(), personality: answers[1]!.trim(), work: answers[2]!.trim() }));
    }
  });
  draw();
  return {
    el,
    sync(eligible: boolean) { el.hidden = dismissed || !eligible; },
    redraw: draw,
  };
}
