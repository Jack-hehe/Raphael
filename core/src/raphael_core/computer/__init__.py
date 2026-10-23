"""Raphael operates the owner's own PC: the apps on the real desktop, through UI Automation.

There is no virtual machine: what Raphael clicks is what the owner sees, on the computer the owner
sits at. So this is a foreground task, never a background one, and it has rules of its own:

- **Presence.** Only a request made at this computer (spoken, or typed in the app) can start a run
  (`tools.PRESENCE_TOOLS`, `needs_presence`). A message from a phone cannot, the background worker
  is never offered these tools (`background.snapshot`), and the gate refuses them to it.
- **The shell does the work.** The core cannot reach UI Automation: the Tauri shell can, and owns
  the overlay, the device settings and the keyboard hook. The core asks it through a narrow bridge
  (`bridge`): a `computer.request` notification, answered by the shell's `computer.reply` request.
- **Per-app permission.** The first time a run wants to look at or act in an app, the owner is
  asked 《問。是否允許操作「Excel」？》 with three answers: allow this time, always allow, or not.
  Always-allowed apps are kept, by executable name, in the shell's device settings.
- **What sends, deletes or buys asks every time** (T2), judged by the name of the element, the
  keys, and what the model itself declares (`safety`).
- **Hand-off.** A password field, a sign-in, a payment form or an elevated (administrator) window
  stops the run: 《告。這一步需要主人親自操作。》.
- **Visible and stoppable.** The overlay shows 「操作中」 and the current step; any real (not
  injected) mouse or keyboard input of the owner stops the run at once, and so do Esc and Right Alt:
  《了。已停止。》.
- **Bounded.** At most 20 actions, three minutes of work and a cost cap per run (`run.RunLimits`).
- **Private.** What the screen shows (window titles, element names and values, typed text,
  snapshots) lives in memory for the length of the run only: it is never logged and never written
  to the database. The steps keep the action, the app's name and how it ended.

Screenshots and a vision model are a later phase: the bridge and the tools leave room for a `see`
operation, which does not exist yet.
"""
