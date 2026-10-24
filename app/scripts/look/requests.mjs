export function requestDisposition(url, origin) {
  if (new URL(url).pathname === "/favicon.ico") return "empty";
  return url.startsWith(`${origin}/`) || /^(data:|blob:|about:)/.test(url) ? "continue" : "block";
}
