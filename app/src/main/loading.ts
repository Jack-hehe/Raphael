import { t } from "./i18n";
import { esc } from "./ui";

/** Keep the initial layout legible while a page waits for the local core. */
export const loadingHTML = (): string =>
  `<div class="page-loading" role="status" aria-label="${esc(t("common.loading"))}">` +
  `<span class="loading-line"></span><span class="loading-line"></span><span class="loading-line"></span></div>`;
