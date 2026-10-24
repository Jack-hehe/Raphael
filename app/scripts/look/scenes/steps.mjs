export const click = selector => ({ action: "click", selector });
export const hover = selector => ({ action: "hover", selector });
export const type = (selector, text) => ({ action: "type", selector, text });
export const wait = selector => ({ action: "wait", selector });
export const check = name => ({ action: "check", name });
export const key = value => ({ action: "key", key: value });
export const scroll = selector => ({ action: "scroll", selector });
export const waitScrollEnd = selector => ({ action: "wait-scroll-end", selector });
export const page = name => click(`#nav .nav[data-go="${name}"]`);
export const settings = pane => [page("settings"), wait(".modal-scrim [data-pane]"), click(`.modal-scrim [data-pane="${pane}"]`)];
export const scene = (name, description, steps = [], fixture = {}) => ({ name, description, steps, fixture });

// The compact member/room picker exposes the same destinations as the wide sidebar.
export const member = id => ({ action: "member", id });
export const room = id => ({ action: "room", id });
export const newRoom = () => ({ action: "new-room" });
export const search = text => ({ action: "search", text });
