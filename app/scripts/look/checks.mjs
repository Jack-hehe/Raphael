// Browser assertions run against the real app after the scene's user interactions.
export function checkRendered(name) {
  const visible = element => element && element.getBoundingClientRect().width > 0 && element.getBoundingClientRect().height > 0;
  const require = (condition, message) => { if (!condition) throw new Error(`${name}: ${message}`); };
  if (name === "conversation-picker") {
    const list = document.querySelector("#app-popover .conversation-menu-list");
    require(visible(list), "conversation list is visible");
    require(list.scrollWidth <= list.clientWidth + 1, `list overflows: ${list.scrollWidth} > ${list.clientWidth}`);
    const button = list.querySelector("[data-conversation-new]"), box = button.getBoundingClientRect(), parent = list.getBoundingClientRect();
    require(box.left >= parent.left && box.right <= parent.right + 1, "new conversation button fits inside the list");
    return { check: name, width: list.clientWidth, scrollWidth: list.scrollWidth };
  }
  if (name === "calendar-chips") {
    const chips = [...document.querySelectorAll(".au-occurrence")];
    require(chips.length > 0, "calendar has event chips");
    for (const chip of chips) {
      const time = chip.querySelector("time, small"), face = chip.querySelector(".av"), label = chip.querySelector("b");
      const bounds = chip.getBoundingClientRect();
      require(visible(face), "member face remains visible in narrow chips");
      require(visible(time) && /^\d{2}:\d{2}$/.test(time.textContent), `complete compact time: ${time?.textContent}`);
      const range = document.createRange(); range.selectNodeContents(time);
      const text = range.getBoundingClientRect();
      require(text.left >= bounds.left + 1 && text.right <= bounds.right - 1 && text.top >= bounds.top && text.bottom <= bounds.bottom,
        `time ${time.textContent} fits chip (${Math.round(text.width)}px text, ${Math.round(bounds.width)}px chip)`);
      require(chip.title.includes(label.textContent) && chip.title.includes(time.textContent), "tooltip preserves full title and time");
      if (visible(label)) require(getComputedStyle(label).textOverflow === "ellipsis", "long visible title uses ellipsis");
    }
    return { check: name, chips: chips.length };
  }
  throw new Error(`Unknown rendered check: ${name}`);
}
