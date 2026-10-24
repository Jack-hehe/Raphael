// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from "vitest";
const api = vi.hoisted(() => ({ settingsSet: vi.fn() }));
vi.mock("./api", () => api);
import { applySkin, createOwnerControls, ownerButtonHTML, ownerProfileHTML, SKINS, skinsHTML } from "./owner-settings";
import type { BrainSettings } from "./types";

afterEach(() => document.body.replaceChildren());
it("previews the You portrait in the profile and never uses a monogram", () => {
  const photo = "data:image/png;base64,aGVsbG8=";
  const settings = { owner_name: "<Jack>", owner_about: "Brief & kind", owner_photo: photo } as BrainSettings;
  expect(ownerProfileHTML(settings)).toContain("&lt;Jack&gt;");
  expect(ownerProfileHTML(settings)).toContain(photo);
  expect(ownerProfileHTML(settings)).toContain('class="owner-photo-preview"');
  expect(ownerProfileHTML(null)).toContain("<svg");
  expect(ownerButtonHTML(photo)).toContain('<img class="owner-photo"');
  expect(ownerButtonHTML("https://outside.example/photo")).not.toContain("<img");
  expect(ownerButtonHTML()).toContain("<svg");
});
it("applies explicit and system appearances to every skin", () => {
  for (const skin of SKINS) {
    for (const systemDark of [false, true]) {
      for (const appearance of ["system", "light", "dark"] as const) {
        applySkin(skin, appearance, systemDark);
        expect(document.documentElement.dataset.skin).toBe(skin);
        expect(document.documentElement.dataset.theme).toBe(appearance === "system" ? systemDark ? "dark" : "light" : appearance);
      }
    }
  }
  expect(SKINS).toContain("catppuccin");
  expect(skinsHTML(null).match(/data-skin-choice=/g)).toHaveLength(SKINS.length);
});
it("keeps both fixed preview variants identical when the active skin or mode changes", () => {
  const host = document.createElement("div"); document.body.append(host);
  const previews = (skin: typeof SKINS[number]): string[] => {
    host.innerHTML = skinsHTML({ skin } as BrainSettings);
    expect(host.querySelectorAll('.skin-sample[data-mode="light"]')).toHaveLength(SKINS.length);
    expect(host.querySelectorAll('.skin-sample[data-mode="dark"]')).toHaveLength(SKINS.length);
    return [...host.querySelectorAll(".skin-preview")].map(el => el.outerHTML);
  };
  const before = previews("default");
  for (const skin of SKINS) {
    for (const mode of ["light", "dark"] as const) {
      applySkin(skin, mode, false);
      expect(previews(skin)).toEqual(before);
    }
  }
});
it("persists profile and appearance edits in order through the core", async () => {
  const host = document.createElement("div"); host.innerHTML = ownerProfileHTML({} as BrainSettings) + skinsHTML({} as BrainSettings); document.body.append(host);
  api.settingsSet.mockImplementation(async (key, value) => ({ settings: { [key]: value } }));
  const saved = vi.fn(), controls = createOwnerControls(host, saved);
  const input = host.querySelector<HTMLInputElement>("[data-owner=owner_name]")!; input.value = "Jack";
  await controls.change(input);
  controls.click(host.querySelector("[data-skin-choice=dusk]")!);
  await vi.waitFor(() => expect(saved).toHaveBeenCalledTimes(2));
  expect(api.settingsSet.mock.calls.map(call => call.slice(0, 2))).toEqual([["owner_name", "Jack"], ["skin", "dusk"]]);
});
