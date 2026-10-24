// @vitest-environment happy-dom
import { expect, test, vi } from "vitest";
vi.mock("./api", () => ({ core: vi.fn() }));
import * as api from "./api";
import { createMessageSearch } from "./message-search";
import { setLocale } from "./i18n";

test("cross-conversation results escape markup, mark matches and navigate with the conversation", async () => {
  setLocale("en");
  vi.mocked(api.core).mockResolvedValue({ turns: [{ id: 9, member_id: 3, conversation_id: 7, conversation_title: "Budget", question: "cobalt <script>", text: "A cobalt report", created_at_ms: 1000 }] });
  const host = document.createElement("div"), open = vi.fn();
  const search = createMessageSearch(host, { face: () => "", name: () => "Researcher <b>", open });
  search.draw("cobalt", "<button>Team member</button>");
  await vi.waitFor(() => expect(host.querySelector("mark")?.textContent).toBe("cobalt"));
  expect(host.querySelector("script")).toBeNull();
  expect(host.textContent).toContain("Budget");
  expect(host.querySelector(".message-search-title")?.textContent).toContain("Researcher <b>");
  expect(host.querySelector(".message-search-title b b")).toBeNull();
  host.querySelector<HTMLButtonElement>("[data-message-result]")!.click();
  expect(open).toHaveBeenCalledWith(3, 7, 9);
  search.draw("", "<b>Members again</b>");
  expect(host.textContent).toBe("Members again");
});
