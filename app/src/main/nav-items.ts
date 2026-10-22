// The pages of the sidebar (main.ts): a button for each, its icon or Raphael's face, then its name.
// The name has a class of its own (`nav-t`): Raphael's face is a span too (avatar.ts), and a new
// language must name the page without writing over his face.

/** One page of the sidebar: `lead` is its icon, or Raphael's face for 拉斐爾, and `name` its name. */
export const navItemHTML = (id: string, name: string, lead: string): string =>
  `<button type="button" class="nav" data-go="${id}" aria-current="false">${lead}<span class="nav-t">${name}</span></button>`;

/** Names the page `id` of the sidebar `nav` in the language of the window. */
export function labelNavItem(nav: ParentNode, id: string, name: string): void {
  nav.querySelector<HTMLElement>(`[data-go="${id}"] .nav-t`)!.textContent = name;
}
