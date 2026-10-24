/** One app tooltip shared by the message actions and composer controls. */
export function messageTooltip(host: HTMLElement): () => void {
  const tip = document.createElement("div");
  tip.className = "message-tooltip";
  tip.id = `message-tip-${crypto.randomUUID()}`;
  tip.setAttribute("role", "tooltip");
  tip.hidden = true;
  host.append(tip);
  let target: HTMLElement | null = null;
  const hide = (): void => {
    target?.removeAttribute("aria-describedby");
    target = null;
    tip.hidden = true;
  };
  const show = (event: Event): void => {
    const button = (event.target as Element).closest<HTMLElement>("[data-tooltip]");
    if (!button || !host.contains(button)) return;
    hide();
    target = button;
    tip.textContent = button.dataset.tooltip!;
    tip.hidden = false;
    button.setAttribute("aria-describedby", tip.id);
    const box = button.getBoundingClientRect(), size = tip.getBoundingClientRect();
    tip.style.left = `${Math.max(8, Math.min(window.innerWidth - size.width - 8, box.left + (box.width - size.width) / 2))}px`;
    tip.style.top = `${Math.max(8, box.top - size.height - 6)}px`;
  };
  host.addEventListener("pointerover", show);
  host.addEventListener("focusin", show);
  host.addEventListener("pointerout", hide);
  host.addEventListener("focusout", hide);
  host.addEventListener("scroll", hide, true);
  host.addEventListener("keydown", hide);
  return hide;
}
