// @vitest-environment happy-dom
import { expect, test, vi } from "vitest";
const api = vi.hoisted(() => ({ membersList: vi.fn(), membersUpdate: vi.fn() }));
vi.mock("./api", () => api);
import { createHiddenMembers } from "./hidden-members";
const flush = async () => { for (let n = 0; n < 12; n++) await Promise.resolve(); };
test("General settings restore a hidden member and refresh the list", async () => {
  const root = document.createElement("div"); root.innerHTML = "<section data-hidden-members></section>";
  const controller = createHiddenMembers(); controller.mount(root);
  api.membersList.mockResolvedValueOnce({members: [{id: 3, name: "Mia", hidden: true}, {id: 4, name: "Alex", hidden: false}]})
    .mockResolvedValue({members: [{id: 3, name: "Mia", hidden: false}]});
  api.membersUpdate.mockResolvedValue({}); await controller.load();
  expect(root.textContent).toContain("Mia"); expect(root.textContent).not.toContain("Alex");
  root.querySelector<HTMLButtonElement>("[data-restore-member]")!.click(); await flush();
  expect(api.membersUpdate).toHaveBeenCalledWith(3, {hidden: false});
  expect(root.querySelector("[data-restore-member]")).toBeNull();
});
test("a stale read cannot replace a newer hidden member list", async () => {
  let resolve!: (value: unknown) => void;
  api.membersList.mockReset().mockReturnValueOnce(new Promise(done => resolve = done))
    .mockResolvedValueOnce({members: [{id: 4, name: "Alex", hidden: true}]});
  const root = document.createElement("div"); root.innerHTML = "<section data-hidden-members></section>";
  const controller = createHiddenMembers(); controller.mount(root);
  const first = controller.load(); await controller.load(); resolve({members: [{id: 3, name: "Mia", hidden: true}]}); await first;
  expect(root.textContent).toContain("Alex"); expect(root.textContent).not.toContain("Mia");
});
