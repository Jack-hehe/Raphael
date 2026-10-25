# Dictation

## Sub-features

Activity/shortcuts, dated history, dictionary and connection notices; native
recording, recognition, cleanup, paste and correction learning. Conversation voice
and Alt+Space agent questions share cleanup and cleanup spend accounting, including
the short-clean-sentence shortcut, without a history row or target paste.

## How to get to it (user POV)

Choose Dictation, then use the vertical icon-and-label list on the left for
Overview/History/Dictionary. Up/Down/Home/End change the selected tab while retaining
keyboard focus; selection remains remembered when returning to the page. Content
stays beside the navigation at both widths. An active heatmap day opens its
history. Settings > Dictation configures recognition/shortcuts. Actual dictation
begins with the configured global key while a target field has focus.

## Driving it with look

```powershell
npm run look -- dictation-overview dictation-history dictation-history-day dictation-dictionary dictation-keyboard dictation-notice dictation --theme both --width 1120,720
npm run look -- overlay-transcribing overlay-cleaning --theme both --width 1120,720
```

Keep tab/heatmap/day-filter/dictionary/settings PNGs and report; check the filter
matches fixture history. The navigation assertion checks vertical geometry, one
selected tab, its visible panel and content overflow; the keyboard scene ends on Dictionary. Overlay scenes drive the real overlay.html entry through synthetic native events;
check the original percentage caption during transcription and cleanup. Browser
scenes cannot record/paste. For an authorized
native test focus a disposable field, press the configured key, speak a known
harmless sentence, include a pause, and stop explicitly with the key or Esc. Observe
overlay lifetime, recognized/cleaned text, target paste and matching history. Test
cancellation separately. For agent voice, check polished composer text / Alt+Space
question delivery, cleanup spend, no dictation history and no target paste. Keep metadata and consented synthetic text only; never log
private field contents, keystrokes, pasted text or passwords for correction learning.

## Gotchas

The fixture's populated history day is 2026-10-24; the first heatmap cell may have no
history. Mock devices/status do not prove permissions, STT or global hotkeys. Silence
must not close recording; only explicit stop/cancel or genuine microphone/STT failure
may stop it. Preserve password/elevated-window/field-scope/60-second learning safeguards.
