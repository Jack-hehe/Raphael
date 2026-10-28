// What a person can ask Raphael for, and what each thing needs before it works. The guide lists
// these as a checklist; the steps and the lessons that follow are worked out from what is ticked
// (flow.ts), so a new ability is one more entry here. Ticking is a plan for the guide, not a
// switch: an ability that is not ticked is still on, it only has no setup and no lesson.

import { t } from "../i18n";
import type { IconName } from "../ui";

/** What has to be set up, each in a step of its own. */
export type Need = "key" | "mic" | "connections" | "telegram";

/** The order of the setup steps, whatever the order of the abilities. */
export const NEED_ORDER: readonly Need[] = ["key", "mic", "connections", "telegram"];

/** The five lessons of the practice, in their order. */
export type LessonId = "key" | "dictate" | "cleanup" | "ask" | "act";

export const LESSON_ORDER: readonly LessonId[] = ["key", "dictate", "cleanup", "ask", "act"];

/** The lessons that talk to Raphael's AI: without a key they are left out. */
export const KEY_LESSONS: readonly LessonId[] = ["cleanup", "ask", "act"];

export type AbilityId = "dictate" | "cleanup" | "ask" | "act" | "connect" | "phone";

export interface Ability {
  id: AbilityId;
  icon: IconName;
  needs: readonly Need[];
  lessons: readonly LessonId[];
  /** Ticked when the guide opens for the first time. */
  byDefault: boolean;
  name(): string;
  desc(): string;
}

export const ABILITIES: readonly Ability[] = [
  { id: "dictate", icon: "mic", needs: ["mic"], lessons: ["key", "dictate"], byDefault: true, name: () => t("onboarding.abilityDictate"), desc: () => t("onboarding.abilityDictateDesc") },
  { id: "cleanup", icon: "pencil", needs: ["key", "mic"], lessons: ["cleanup"], byDefault: true, name: () => t("onboarding.abilityCleanup"), desc: () => t("onboarding.abilityCleanupDesc") },
  { id: "ask", icon: "chat", needs: ["key", "mic"], lessons: ["ask"], byDefault: true, name: () => t("onboarding.abilityAsk"), desc: () => t("onboarding.abilityAskDesc") },
  { id: "act", icon: "monitor", needs: ["key", "mic"], lessons: ["act"], byDefault: true, name: () => t("onboarding.abilityAct"), desc: () => t("onboarding.abilityActDesc") },
  { id: "connect", icon: "link", needs: ["key", "connections"], lessons: [], byDefault: false, name: () => t("onboarding.abilityConnect"), desc: () => t("onboarding.abilityConnectDesc") },
  { id: "phone", icon: "send", needs: ["key", "telegram"], lessons: [], byDefault: false, name: () => t("onboarding.abilityPhone"), desc: () => t("onboarding.abilityPhoneDesc") },
];
