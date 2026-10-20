import { describe, expect, test } from "vitest";
import { NAV_LIMIT, NavHistory, Navigation, samePage, samePlace, type NavTarget, type Place } from "./nav-history";

const home: Place = { page: "home" };
const agent: Place = { page: "agent" };
const history: Place = { page: "history" };
const dictionary: Place = { page: "dictionary" };
/** The history opened at one day, as a day of the activity grid opens it. */
const day = (date: string): Place => ({ page: "history", day: date });
/** The settings panel open over a page, at one of its tabs. */
const panel = (page: Place["page"], pane: NonNullable<Place["pane"]>): Place => ({ page, pane });

/** Pushes the places in order. */
function visited(...places: Place[]): NavHistory {
  const trail = new NavHistory();
  places.forEach((place) => trail.push(place));
  return trail;
}

describe("the places visited", () => {
  test("with none visited there is nowhere to go", () => {
    const trail = new NavHistory();
    expect(trail.back()).toBeNull();
    expect(trail.forward()).toBeNull();
  });

  test("back and forward walk through them in the order they were visited", () => {
    const trail = visited(home, history, dictionary);
    expect(trail.back()).toEqual(history);
    expect(trail.back()).toEqual(home);
    expect(trail.back()).toBeNull();
    expect(trail.forward()).toEqual(history);
    expect(trail.forward()).toEqual(dictionary);
    expect(trail.forward()).toBeNull();
  });

  test("a place visited after going back forgets the ones that were ahead", () => {
    const trail = visited(home, history, dictionary);
    trail.back();
    trail.back();
    trail.push(agent);
    expect(trail.forward()).toBeNull();
    expect(trail.back()).toEqual(home);
    expect(trail.forward()).toEqual(agent);
  });

  test("going to the place it is at adds nothing", () => {
    const trail = visited(home, home, history, history);
    expect(trail.back()).toEqual(home);
    expect(trail.back()).toBeNull();
  });

  test("going to the place it is at in the middle leaves the places ahead", () => {
    const trail = visited(home, history, dictionary);
    trail.back();
    trail.push(history);
    expect(trail.forward()).toEqual(dictionary);
  });

  test("the same page can come again once another has come between", () => {
    const trail = visited(home, history, home);
    expect(trail.back()).toEqual(history);
    expect(trail.back()).toEqual(home);
  });

  test("keeps the newest places and lets the oldest go", () => {
    const trail = new NavHistory(3);
    [home, agent, history, dictionary].forEach((place) => trail.push(place));
    expect(trail.back()).toEqual(history);
    expect(trail.back()).toEqual(agent);
    expect(trail.back()).toBeNull();
  });

  test("keeps fifty places", () => {
    expect(NAV_LIMIT).toBe(50);
    const trail = new NavHistory();
    for (let n = 0; n < 80; n++) trail.push(day(`day ${n}`));
    let steps = 0;
    while (trail.back()) steps++;
    expect(steps).toBe(49);
  });

  test("a place that was handed over is not changed by later changes to it", () => {
    const place: Place = { page: "history", day: "2026-10-14" };
    const trail = visited(home, place);
    place.day = "2026-10-15";
    trail.back();
    expect(trail.forward()).toEqual(day("2026-10-14"));
  });
});

describe("the settings panel", () => {
  test("opening it is a step: back closes it and leaves the page under it, forward opens it again", () => {
    const trail = visited(home, panel("home", "general"));
    expect(trail.back()).toEqual({ page: "home" });
    expect(trail.forward()).toEqual({ page: "home", pane: "general" });
  });

  test("the page under the panel is the same page, so it is not shown again", () => {
    expect(samePage(panel("history", "usage"), history)).toBe(true);
    expect(samePage(panel("history", "usage"), panel("history", "about"))).toBe(true);
    expect(samePage(home, history)).toBe(false);
    expect(samePage(panel("home", "usage"), panel("history", "usage"))).toBe(false);
  });

  test("a place is the same only with the same tab open", () => {
    expect(samePlace(panel("home", "usage"), panel("home", "usage"))).toBe(true);
    expect(samePlace(panel("home", "usage"), panel("home", "about"))).toBe(false);
    expect(samePlace(panel("home", "usage"), home)).toBe(false);
  });

  test("the tab chosen inside the panel is the one that comes back", () => {
    const trail = visited(home, panel("home", "general"));
    trail.replace(panel("home", "usage"));
    expect(trail.back()).toEqual(home);
    expect(trail.forward()).toEqual(panel("home", "usage"));
  });

  test("a panel closed with its own button leaves a page that going back would not change", () => {
    const trail = visited(history, home, panel("home", "general"));
    trail.replace(home);
    expect(trail.back()).toEqual(history);
    expect(trail.back()).toBeNull();
  });

  test("with nothing before the page, a panel closed with its own button leaves nowhere to go", () => {
    const trail = visited(home, panel("home", "general"));
    trail.replace(home);
    expect(trail.back()).toBeNull();
    expect(trail.forward()).toBeNull();
  });

  test("a panel closed with its own button, with the same page also ahead, leaves nowhere to go either way", () => {
    // Home, the panel over it, then Home again (the tray was asked for it with the panel open)
    const trail = visited(home, panel("home", "general"), home);
    trail.back();
    trail.replace(home);
    expect(trail.forward()).toBeNull();
    expect(trail.back()).toBeNull();
  });

  test("opening and closing it again and again does not push the pages out of the history", () => {
    const trail = new NavHistory(3);
    trail.push(home);
    trail.push(history);
    for (let n = 0; n < 10; n++) {
      trail.push(panel("history", "general"));
      trail.replace(history);
    }
    expect(trail.back()).toEqual(home);
    expect(trail.forward()).toEqual(history);
    expect(trail.forward()).toBeNull();
  });

  test("a panel that was left open when the person moved on is there again going back", () => {
    const trail = visited(home, panel("home", "usage"), dictionary);
    expect(trail.back()).toEqual(panel("home", "usage"));
    expect(trail.back()).toEqual(home);
  });
});

describe("the history opened at a day", () => {
  test("comes back at that day", () => {
    const trail = visited(home, day("2026-10-14"));
    expect(trail.back()).toEqual(home);
    expect(trail.forward()).toEqual(day("2026-10-14"));
  });

  test("is another page from the history of all days, and the panel over it keeps the day", () => {
    expect(samePage(day("2026-10-14"), history)).toBe(false);
    expect(samePage(day("2026-10-14"), day("2026-10-15"))).toBe(false);
    expect(samePage(day("2026-10-14"), { ...day("2026-10-14"), pane: "about" })).toBe(true);
  });

  test("shows all days again when the person took the day off before leaving", () => {
    const trail = visited(home, day("2026-10-14"));
    trail.replace(history);
    trail.back();
    expect(trail.forward()).toEqual(history);
  });
});

/** A window that is at one place, and notes what it is asked to show and which pages it loads. */
interface FakeWindow extends NavTarget {
  at: Place | null;
  guide: boolean;
  shown: Place[];
  loaded: string[];
}

function fakeWindow(): FakeWindow {
  const window: FakeWindow = {
    at: null,
    guide: false,
    shown: [],
    loaded: [],
    inGuide: () => window.guide,
    where: () => window.at,
    // What main.ts does: a page is loaded again only when it is another one.
    show(place) {
      if (!window.at || !samePage(window.at, place)) window.loaded.push(place.page);
      window.at = place;
      window.shown.push(place);
    },
  };
  return window;
}

/** The person goes to a place, as `go` in main.ts does. */
function visit(navigation: Navigation, window: FakeWindow, place: Place): void {
  navigation.visit(() => {
    window.at = place;
  });
}

describe("the window's history", () => {
  test("a step shows the place it lands on and records nothing", () => {
    const window = fakeWindow();
    const navigation = new Navigation(window);
    [home, history, dictionary].forEach((place) => visit(navigation, window, place));
    navigation.step("back");
    navigation.step("back");
    navigation.step("forward");
    navigation.step("forward");
    navigation.step("forward");
    expect(window.shown).toEqual([history, home, history, dictionary]);
    expect(window.at).toEqual(dictionary);
  });

  test("a place visited after a step back forgets what was ahead", () => {
    const window = fakeWindow();
    const navigation = new Navigation(window);
    [home, history, dictionary].forEach((place) => visit(navigation, window, place));
    navigation.step("back");
    visit(navigation, window, agent);
    navigation.step("forward");
    expect(window.shown).toEqual([history]);
    navigation.step("back");
    expect(window.shown).toEqual([history, history]);
    expect(window.at).toEqual(history);
  });

  test("going to the place the window is at records nothing", () => {
    const window = fakeWindow();
    const navigation = new Navigation(window);
    [home, home, history, history].forEach((place) => visit(navigation, window, place));
    navigation.step("back");
    navigation.step("back");
    expect(window.shown).toEqual([home]);
  });

  test("the first-run guide is no part of the history: steps do nothing while it shows", () => {
    const window = fakeWindow();
    const navigation = new Navigation(window);
    window.guide = true;
    navigation.step("back");
    navigation.step("forward");
    expect(window.shown).toEqual([]);
    window.guide = false;
    visit(navigation, window, home); // the guide is done: Home is the first place
    navigation.step("back");
    expect(window.shown).toEqual([]);
    visit(navigation, window, history);
    window.guide = true;
    navigation.step("back");
    expect(window.shown).toEqual([]);
    window.guide = false;
    navigation.step("back");
    expect(window.shown).toEqual([home]);
  });

  test("back from the open settings panel closes it and does not load the page under it again", () => {
    const window = fakeWindow();
    const navigation = new Navigation(window);
    [home, history, panel("history", "usage")].forEach((place) => visit(navigation, window, place));
    navigation.step("back");
    expect(window.shown).toEqual([history]);
    expect(window.loaded).toEqual([]);
    navigation.step("forward");
    expect(window.shown).toEqual([history, panel("history", "usage")]);
    expect(window.loaded).toEqual([]);
    navigation.step("back");
    navigation.step("back");
    expect(window.shown[3]).toEqual(home);
    expect(window.loaded).toEqual(["home"]);
  });

  test("a tab chosen in the panel is the one it comes back at", () => {
    const window = fakeWindow();
    const navigation = new Navigation(window);
    visit(navigation, window, home);
    visit(navigation, window, panel("home", "general"));
    window.at = panel("home", "usage"); // the person clicked the tab inside the panel
    visit(navigation, window, history);
    navigation.step("back");
    expect(window.shown).toEqual([panel("home", "usage")]);
  });

  test("a panel closed with its own button does not come back, and back goes to the page before", () => {
    const window = fakeWindow();
    const navigation = new Navigation(window);
    [history, home, panel("home", "general")].forEach((place) => visit(navigation, window, place));
    window.at = home; // the person pressed the panel's close button, which does not take a step
    navigation.step("back");
    expect(window.shown).toEqual([history]);
    navigation.step("forward");
    navigation.step("forward");
    expect(window.shown).toEqual([history, home]);
  });

  test("a panel closed with its own button leaves nothing behind when the person goes on", () => {
    const window = fakeWindow();
    const navigation = new Navigation(window);
    visit(navigation, window, home);
    visit(navigation, window, panel("home", "general"));
    window.at = home;
    visit(navigation, window, history);
    navigation.step("back");
    expect(window.shown).toEqual([home]);
    navigation.step("back");
    expect(window.shown).toEqual([home]);
  });

  test("the history opened at a day comes back at that day", () => {
    const window = fakeWindow();
    const navigation = new Navigation(window);
    visit(navigation, window, home);
    visit(navigation, window, day("2026-10-14"));
    navigation.step("back");
    navigation.step("forward");
    expect(window.shown).toEqual([home, day("2026-10-14")]);
    expect(window.loaded).toEqual(["home", "history"]);
  });

  test("a day taken off before leaving is left off when it comes back", () => {
    const window = fakeWindow();
    const navigation = new Navigation(window);
    visit(navigation, window, home);
    visit(navigation, window, day("2026-10-14"));
    window.at = history; // the person pressed the chip's button
    visit(navigation, window, dictionary);
    navigation.step("back");
    expect(window.shown).toEqual([history]);
  });

  test("keeps no more places than it is given room for", () => {
    const window = fakeWindow();
    const navigation = new Navigation(window, 2);
    [home, history, dictionary].forEach((place) => visit(navigation, window, place));
    navigation.step("back");
    navigation.step("back");
    expect(window.shown).toEqual([history]);
  });
});
