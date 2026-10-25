# Dictation

Global questions use Raphael's persistent quick conversation. Public progress/thinking
comes from the same stored turn stream as the conversation, newest line only, in the
existing 14px caption position. No rotating placeholder status is sent. The API fast
router remains available; CLI quick questions skip auxiliary routing and native session
resume, consuming only the recent quick context and that conversation's own summary.
Native acceptance must check stale-session rejection, foreground suppression,
hotkey/transcript timing and actual first audio.

```powershell
npm run look -- overlay-step --theme both --width 1120,720,1920x1080
```

Offline latency plumbing: `uv run python -m raphael_core.tools.quick_latency --fake --count 9` from core/.
Fake timings do not establish live speed. The owner runs without `--fake` for model
timings using recording execution and silent first-audio simulation, then compares
real hotkey/STT/audio logs. Only native logs prove actual transcription/playback speed.

## Sub-features

Four separate totals, activity/shortcuts, horizontal tabs, dated history, dictionary and connection notices; native
recording, recognition, cleanup, paste and correction learning. Conversation voice
and Alt+Space agent questions share cleanup and cleanup spend accounting, including
the short-clean-sentence shortcut, without a history row or target paste.

## How to get to it (user POV)

Choose Dictation, then use the compact horizontal Overview/History/Dictionary tabs beside the title. Left/Right/Home/End change the selected tab while retaining keyboard focus; selection and legacy history/dictionary deep links remain available. Four separate stat cards sit above the activity heatmap, with shortcuts on the right on desktop. At 720 the totals form two rows and shortcuts follow the activity card. An active heatmap day opens its
history. Settings > Dictation configures recognition/shortcuts. Actual dictation
begins with the configured global key while a target field has focus.

## Driving it with look

```powershell
npm run look -- dictation-overview dictation-history dictation-history-day dictation-dictionary dictation-keyboard dictation-notice dictation --theme both --width 1120,720,1920x1080
npm run look -- overlay-transcribing overlay-cleaning --theme both --width 1120,720,1920x1080
npm run look -- composer-learn member-composer-learn room-composer-learn team-composer-learn --theme both --width 1120,720,1920x1080
```

Keep tab/heatmap/day-filter/dictionary/settings PNGs and report; check the filter
matches fixture history. The navigation assertion checks horizontal geometry, one
selected tab, its visible panel and content overflow; the keyboard scene exercises both arrows and Home/End, ending on Dictionary. Overlay scenes drive the real overlay.html entry through synthetic native events;
check the original percentage caption during transcription and cleanup. Browser
scenes cannot record/paste. For an authorized
native test focus a disposable field, press the configured key, speak a known
harmless sentence, include a pause, and stop explicitly with the key or Esc. Observe
overlay lifetime, recognized/cleaned text, target paste and matching history. Test
cancellation separately. For agent voice, check polished composer text / Alt+Space
question delivery, cleanup spend, no dictation history and no target paste. Keep metadata and consented synthetic text only; never log
private field contents, keystrokes, pasted text or passwords for correction learning.

The four composer-learning scenes seed synthetic dictation through the existing native event
boundary, correct Open Mouse Bot to OpenMausBot, and send through the real composer.
They assert exactly one dictionary.learn call with the complete correction, exactly one
localized notice clear of the composer, and the unchanged single gold-emblem / bare-plane slot before and after send.
Only dictation seeds learning; ordinary typing and clipboard paste do not. Native microphone,
external-editor correction tracking and persistence still require isolated native acceptance.

History and dictionary rows put deletion in the overflow menu (R-UI-11). The dictionary
scene opens that menu and dismisses it before capture; the real-entry DOM test also
deletes a word through it and restores it with Undo.

## Gotchas

The fixture's populated history day is 2026-10-24; the first heatmap cell may have no
history. Mock devices/status do not prove permissions, STT or global hotkeys. Silence
must not close recording; only explicit stop/cancel or genuine microphone/STT failure
may stop it. Preserve password/elevated-window/field-scope/60-second learning safeguards.
