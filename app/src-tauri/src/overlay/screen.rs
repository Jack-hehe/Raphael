//! What happens to the screen while Raphael runs, written to the log as it happens: the display
//! turning off, dimming and coming back, the session locking and unlocking, the computer going to
//! sleep and waking up, the display settings, the DPI and the work area changing; and the
//! webview's own processes failing, its GPU process above all.
//!
//! These are the moments after which a transparent window's webview could stop reaching the
//! screen while its page goes on drawing: after a GPU reset, say, WebView2 starts a new GPU
//! process by itself, and nothing else would tell. The page sees its frames and its pixels and
//! the window check (`sight`) sees a window in sight, so an emblem that nobody saw can only be put
//! next to what happened to the screen before it, and these lines give the time of that.
//!
//! Nothing here changes anything: the lines are numbers and fixed words.

// The messages and codes, as Win32 numbers them (WinUser.h, WtsApi32.h).
const WM_SETTINGCHANGE: u32 = 0x001A;
const WM_DISPLAYCHANGE: u32 = 0x007E;
const WM_POWERBROADCAST: u32 = 0x0218;
const WM_WTSSESSION_CHANGE: u32 = 0x02B1;
const WM_DPICHANGED: u32 = 0x02E0;
const WM_DWMCOMPOSITIONCHANGED: u32 = 0x031E;
const PBT_APMSUSPEND: usize = 0x0004;
const PBT_APMRESUMEAUTOMATIC: usize = 0x0012;
const PBT_POWERSETTINGCHANGE: usize = 0x8013;
const SPI_SETWORKAREA: usize = 0x002F;
const WTS_CONSOLE_CONNECT: usize = 0x1;
const WTS_CONSOLE_DISCONNECT: usize = 0x2;
const WTS_REMOTE_CONNECT: usize = 0x3;
const WTS_REMOTE_DISCONNECT: usize = 0x4;
const WTS_SESSION_LOCK: usize = 0x7;
const WTS_SESSION_UNLOCK: usize = 0x8;

/// Something that happened to the screen, from a message to the overlay window.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum ScreenEvent {
    /// `GUID_CONSOLE_DISPLAY_STATE`: 0 off, 1 on, 2 dimmed.
    Display(u32),
    Suspending,
    Resumed,
    /// One of the `WTS_*` codes above.
    Session(usize),
    DisplaySettings {
        width: u32,
        height: u32,
        bits: u32,
    },
    Dpi(u32),
    WorkArea,
    Compositor,
}

/// What the message `message` with `wparam` and `lparam` says about the screen, if anything.
/// `display` is the display state that a `PBT_POWERSETTINGCHANGE` carried, when it was about the
/// display (it is behind a pointer, which the caller reads).
pub(super) fn decode(
    message: u32,
    wparam: usize,
    lparam: isize,
    display: Option<u32>,
) -> Option<ScreenEvent> {
    let low = |value: usize| (value & 0xFFFF) as u32;
    let high = |value: usize| ((value >> 16) & 0xFFFF) as u32;
    match message {
        WM_POWERBROADCAST => match wparam {
            PBT_APMSUSPEND => Some(ScreenEvent::Suspending),
            // Always sent on waking, whether or not someone is at the computer.
            PBT_APMRESUMEAUTOMATIC => Some(ScreenEvent::Resumed),
            PBT_POWERSETTINGCHANGE => display.map(ScreenEvent::Display),
            _ => None,
        },
        WM_WTSSESSION_CHANGE => match wparam {
            WTS_CONSOLE_CONNECT
            | WTS_CONSOLE_DISCONNECT
            | WTS_REMOTE_CONNECT
            | WTS_REMOTE_DISCONNECT
            | WTS_SESSION_LOCK
            | WTS_SESSION_UNLOCK => Some(ScreenEvent::Session(wparam)),
            _ => None,
        },
        WM_DISPLAYCHANGE => Some(ScreenEvent::DisplaySettings {
            width: low(lparam as usize),
            height: high(lparam as usize),
            bits: wparam as u32,
        }),
        WM_DPICHANGED => Some(ScreenEvent::Dpi(low(wparam))),
        WM_SETTINGCHANGE if wparam == SPI_SETWORKAREA => Some(ScreenEvent::WorkArea),
        WM_DWMCOMPOSITIONCHANGED => Some(ScreenEvent::Compositor),
        _ => None,
    }
}

/// The event in words, for the log.
pub(super) fn describe(event: ScreenEvent) -> String {
    match event {
        ScreenEvent::Display(0) => "the display is off".into(),
        ScreenEvent::Display(1) => "the display is on".into(),
        ScreenEvent::Display(2) => "the display is dimmed".into(),
        ScreenEvent::Display(state) => format!("the display is in state {state}"),
        ScreenEvent::Suspending => "the computer is going to sleep".into(),
        ScreenEvent::Resumed => "the computer woke up".into(),
        ScreenEvent::Session(WTS_SESSION_LOCK) => "the session is locked".into(),
        ScreenEvent::Session(WTS_SESSION_UNLOCK) => "the session is unlocked".into(),
        ScreenEvent::Session(WTS_CONSOLE_CONNECT) => "the session is on this screen again".into(),
        ScreenEvent::Session(WTS_CONSOLE_DISCONNECT) => "the session left this screen".into(),
        ScreenEvent::Session(WTS_REMOTE_CONNECT) => "the session is connected remotely".into(),
        ScreenEvent::Session(WTS_REMOTE_DISCONNECT) => {
            "the session is disconnected remotely".into()
        }
        ScreenEvent::Session(code) => format!("the session changed ({code})"),
        ScreenEvent::DisplaySettings {
            width,
            height,
            bits,
        } => format!("the display settings changed ({width}x{height}, {bits} bits per pixel)"),
        ScreenEvent::Dpi(dpi) => format!("the overlay's monitor is at {dpi} DPI now"),
        ScreenEvent::WorkArea => "the work area changed".into(),
        ScreenEvent::Compositor => "the desktop compositor changed (it may have restarted)".into(),
    }
}

/// A failed process of the webview, in words: `kind` is its `COREWEBVIEW2_PROCESS_FAILED_KIND`,
/// `reason` its `COREWEBVIEW2_PROCESS_FAILED_REASON` and `exit_code` how it ended, each when
/// WebView2 said.
pub(super) fn process_failure(
    kind: Option<i32>,
    reason: Option<i32>,
    exit_code: Option<i32>,
) -> String {
    let what = match kind {
        Some(0) => "browser process exited",
        Some(1) => "render process exited",
        Some(2) => "render process stopped responding",
        Some(3) => "render process of a frame exited",
        Some(4) => "utility process exited",
        Some(5) => "sandbox helper process exited",
        Some(6) => "GPU process exited",
        Some(7) => "plugin process exited",
        Some(8) => "plugin broker process exited",
        Some(9) => "unknown process exited",
        Some(_) | None => "process failed",
    };
    let why = match reason {
        Some(0) => "unexpectedly".to_owned(),
        Some(1) => "unresponsive".to_owned(),
        Some(2) => "terminated".to_owned(),
        Some(3) => "crashed".to_owned(),
        Some(4) => "failed to launch".to_owned(),
        Some(5) => "out of memory".to_owned(),
        Some(6) => "its profile was deleted".to_owned(),
        Some(other) => format!("reason {other}"),
        None => "no reason given".to_owned(),
    };
    let ended = exit_code.map_or_else(String::new, |code| format!(", exit code {code}"));
    // WebView2's documentation: a GPU process that exits is made again by itself.
    let then = if kind == Some(6) {
        "; WebView2 starts a new one"
    } else {
        ""
    };
    format!("the webview's {what} ({why}{ended}){then}")
}

// ---------- the window and the webview ----------

#[cfg(windows)]
pub(super) use native::watch;

#[cfg(not(windows))]
pub(super) fn watch(_window: &tauri::WebviewWindow, _hwnd: isize) {}

#[cfg(windows)]
mod native {
    use std::ffi::c_void;

    use tauri::WebviewWindow;
    use webview2_com::{
        Microsoft::Web::WebView2::Win32::{
            COREWEBVIEW2_PROCESS_FAILED_KIND, COREWEBVIEW2_PROCESS_FAILED_REASON,
            ICoreWebView2ProcessFailedEventArgs, ICoreWebView2ProcessFailedEventArgs2,
        },
        ProcessFailedEventHandler,
    };
    use windows::{
        Win32::{
            Foundation::{HANDLE, HWND, LPARAM, LRESULT, WPARAM},
            System::{
                Power::{
                    HPOWERNOTIFY, POWERBROADCAST_SETTING, RegisterPowerSettingNotification,
                    UnregisterPowerSettingNotification,
                },
                RemoteDesktop::{
                    NOTIFY_FOR_THIS_SESSION, WTSRegisterSessionNotification,
                    WTSUnRegisterSessionNotification,
                },
                SystemServices::GUID_CONSOLE_DISPLAY_STATE,
            },
            UI::{
                Shell::{DefSubclassProc, RemoveWindowSubclass, SetWindowSubclass},
                WindowsAndMessaging::{DEVICE_NOTIFY_WINDOW_HANDLE, WM_NCDESTROY},
            },
        },
        core::Interface,
    };

    use super::{PBT_POWERSETTINGCHANGE, WM_POWERBROADCAST, decode, describe, process_failure};
    use crate::overlay::hresult;

    /// This subclass's id on the overlay window (wry has one of its own there).
    const SUBCLASS_ID: usize = 0x5241_5048;

    /// Starts writing down what happens to the screen, from the overlay window `hwnd`, and the
    /// failures of the webview's processes. Call it once, when the window is made.
    pub(in crate::overlay) fn watch(window: &WebviewWindow, hwnd: isize) {
        let installed = window.run_on_main_thread(move || {
            // SAFETY: `hwnd` is the overlay window, which lives as long as the app, and this runs
            // on the thread that owns it, which a subclass and these registrations need.
            unsafe { install(HWND(hwnd as *mut c_void)) };
        });
        if let Err(err) = installed {
            log::info!("overlay: what happens to the screen is not logged: {err}");
        }
        watch_webview(window);
    }

    /// Subclasses the window to hear the messages, and asks for the two that are only sent on
    /// request: the display's power state and the session's changes.
    ///
    /// # Safety
    ///
    /// `hwnd` must be the overlay window, and the caller its thread.
    unsafe fn install(hwnd: HWND) {
        // SAFETY: the caller owns `hwnd`; the subclass gets the registration's handle to undo it
        // when the window goes.
        unsafe {
            let power = match RegisterPowerSettingNotification(
                HANDLE(hwnd.0),
                &GUID_CONSOLE_DISPLAY_STATE,
                DEVICE_NOTIFY_WINDOW_HANDLE,
            ) {
                Ok(handle) => handle.0 as usize,
                Err(err) => {
                    log::info!(
                        "overlay: the display turning off and on is not logged: {}",
                        hresult(&err)
                    );
                    0
                }
            };
            if !SetWindowSubclass(hwnd, Some(screen_proc), SUBCLASS_ID, power).as_bool() {
                log::info!("overlay: what happens to the screen is not logged: no subclass");
                if power != 0 {
                    let _ = UnregisterPowerSettingNotification(HPOWERNOTIFY(power as isize));
                }
                return;
            }
            if let Err(err) = WTSRegisterSessionNotification(hwnd, NOTIFY_FOR_THIS_SESSION) {
                log::info!(
                    "overlay: locking and unlocking are not logged: {}",
                    hresult(&err)
                );
            }
        }
    }

    /// Writes down what a message to the overlay window says about the screen, and passes the
    /// message on. `power` is the display state registration, undone with the subclass.
    unsafe extern "system" fn screen_proc(
        hwnd: HWND,
        message: u32,
        wparam: WPARAM,
        lparam: LPARAM,
        _id: usize,
        power: usize,
    ) -> LRESULT {
        let display = if message == WM_POWERBROADCAST && wparam.0 == PBT_POWERSETTINGCHANGE {
            // SAFETY: Windows sends a `POWERBROADCAST_SETTING` with `PBT_POWERSETTINGCHANGE`.
            unsafe { display_state(lparam) }
        } else {
            None
        };
        if let Some(event) = decode(message, wparam.0, lparam.0, display) {
            log::info!("overlay: screen: {}", describe(event));
        }
        if message == WM_NCDESTROY {
            // The window's last message: what was asked for is undone while it still exists.
            // SAFETY: `hwnd` is still the window, on its thread.
            unsafe {
                let _ = WTSUnRegisterSessionNotification(hwnd);
                if power != 0 {
                    let _ = UnregisterPowerSettingNotification(HPOWERNOTIFY(power as isize));
                }
                let _ = RemoveWindowSubclass(hwnd, Some(screen_proc), SUBCLASS_ID);
            }
        }
        // SAFETY: the message is the window's, passed on as it came.
        unsafe { DefSubclassProc(hwnd, message, wparam, lparam) }
    }

    /// The display state that a `PBT_POWERSETTINGCHANGE` carries, if it is about the display.
    ///
    /// # Safety
    ///
    /// `lparam` must be the message's: null, or a `POWERBROADCAST_SETTING` followed by its
    /// `DataLength` bytes of data.
    unsafe fn display_state(lparam: LPARAM) -> Option<u32> {
        let setting = lparam.0 as *const POWERBROADCAST_SETTING;
        if setting.is_null() {
            return None;
        }
        // SAFETY: the caller's promise. The data is read through the pointer that Windows gave,
        // which covers all of it, not through the one-byte array the struct declares.
        unsafe {
            if (*setting).PowerSetting != GUID_CONSOLE_DISPLAY_STATE
                || ((*setting).DataLength as usize) < size_of::<u32>()
            {
                return None;
            }
            Some((&raw const (*setting).Data).cast::<u32>().read_unaligned())
        }
    }

    /// Writes down each failure of one of the webview's processes. The overlay and the main
    /// window share one WebView2 browser, so its GPU process is theirs too.
    fn watch_webview(window: &WebviewWindow) {
        let watched = window.with_webview(|webview| {
            let handler = ProcessFailedEventHandler::create(Box::new(|_, args| {
                if let Some(args) = args {
                    log::warn!("overlay: {}", failure(&args));
                }
                Ok(())
            }));
            let mut token: i64 = 0;
            // SAFETY: this runs on the main thread, which owns the webview; the token is a valid
            // place for the answer.
            let added = unsafe { webview.controller().CoreWebView2() }
                .and_then(|core| unsafe { core.add_ProcessFailed(&handler, &mut token) });
            match added {
                Ok(()) => log::debug!("overlay: the webview's process failures are logged"),
                Err(err) => log::info!(
                    "overlay: the webview's process failures are not logged: {}",
                    hresult(&err)
                ),
            }
        });
        if let Err(err) = watched {
            log::info!("overlay: the webview's process failures are not logged: {err}");
        }
    }

    /// What failed, from the event's arguments: numbers only, never the process's description
    /// or the path of a module.
    fn failure(args: &ICoreWebView2ProcessFailedEventArgs) -> String {
        let mut kind = COREWEBVIEW2_PROCESS_FAILED_KIND::default();
        // SAFETY: the arguments are valid while the event is handled; each out-parameter is a
        // valid place.
        let kind = unsafe { args.ProcessFailedKind(&mut kind) }
            .ok()
            .map(|()| kind.0);
        let details = args.cast::<ICoreWebView2ProcessFailedEventArgs2>().ok();
        let reason = details.as_ref().and_then(|details| {
            let mut reason = COREWEBVIEW2_PROCESS_FAILED_REASON::default();
            // SAFETY: as above.
            unsafe { details.Reason(&mut reason) }
                .ok()
                .map(|()| reason.0)
        });
        let exit_code = details.as_ref().and_then(|details| {
            let mut code = 0;
            // SAFETY: as above.
            unsafe { details.ExitCode(&mut code) }.ok().map(|()| code)
        });
        process_failure(kind, reason, exit_code)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn power_session_and_display_messages_are_told_apart() {
        assert_eq!(
            decode(WM_POWERBROADCAST, PBT_APMSUSPEND, 0, None),
            Some(ScreenEvent::Suspending)
        );
        assert_eq!(
            decode(WM_POWERBROADCAST, PBT_APMRESUMEAUTOMATIC, 0, None),
            Some(ScreenEvent::Resumed)
        );
        // the second wake-up message, sent when someone is at the computer, says nothing new
        assert_eq!(decode(WM_POWERBROADCAST, 0x7, 0, None), None);
        for state in [0, 1, 2] {
            assert_eq!(
                decode(WM_POWERBROADCAST, PBT_POWERSETTINGCHANGE, 0, Some(state)),
                Some(ScreenEvent::Display(state))
            );
        }
        // a power setting that is not the display's
        assert_eq!(
            decode(WM_POWERBROADCAST, PBT_POWERSETTINGCHANGE, 0, None),
            None
        );
        assert_eq!(
            decode(WM_WTSSESSION_CHANGE, WTS_SESSION_LOCK, 0, None),
            Some(ScreenEvent::Session(WTS_SESSION_LOCK))
        );
        assert_eq!(
            decode(WM_WTSSESSION_CHANGE, WTS_SESSION_UNLOCK, 0, None),
            Some(ScreenEvent::Session(WTS_SESSION_UNLOCK))
        );
        // logon, logoff and the like are not about the screen
        assert_eq!(decode(WM_WTSSESSION_CHANGE, 0x5, 0, None), None);
        assert_eq!(
            decode(WM_DISPLAYCHANGE, 32, (1080 << 16) | 1920, None),
            Some(ScreenEvent::DisplaySettings {
                width: 1920,
                height: 1080,
                bits: 32
            })
        );
        assert_eq!(
            decode(WM_DPICHANGED, (144 << 16) | 144, 0, None),
            Some(ScreenEvent::Dpi(144))
        );
        assert_eq!(
            decode(WM_SETTINGCHANGE, SPI_SETWORKAREA, 0, None),
            Some(ScreenEvent::WorkArea)
        );
        // the other settings that change, many times a day, are not
        assert_eq!(decode(WM_SETTINGCHANGE, 0, 0, None), None);
        assert_eq!(
            decode(WM_DWMCOMPOSITIONCHANGED, 0, 0, None),
            Some(ScreenEvent::Compositor)
        );
        // and the window's everyday messages pass by
        for message in [0x000F, 0x0200, 0x0113, 0x0003, 0x0005] {
            assert_eq!(decode(message, 0, 0, None), None, "{message:#x}");
        }
    }

    #[test]
    fn screen_events_read_as_words_and_numbers() {
        assert_eq!(describe(ScreenEvent::Display(0)), "the display is off");
        assert_eq!(describe(ScreenEvent::Display(1)), "the display is on");
        assert_eq!(describe(ScreenEvent::Display(2)), "the display is dimmed");
        assert_eq!(
            describe(ScreenEvent::Display(7)),
            "the display is in state 7"
        );
        assert_eq!(
            describe(ScreenEvent::Session(WTS_SESSION_UNLOCK)),
            "the session is unlocked"
        );
        assert_eq!(
            describe(ScreenEvent::DisplaySettings {
                width: 1920,
                height: 1080,
                bits: 32
            }),
            "the display settings changed (1920x1080, 32 bits per pixel)"
        );
        assert_eq!(
            describe(ScreenEvent::Dpi(120)),
            "the overlay's monitor is at 120 DPI now"
        );
    }

    #[test]
    fn a_failed_process_is_told_by_its_numbers() {
        assert_eq!(
            process_failure(Some(6), Some(3), Some(-1_073_741_819)),
            "the webview's GPU process exited (crashed, exit code -1073741819); \
             WebView2 starts a new one"
        );
        assert_eq!(
            process_failure(Some(2), Some(1), None),
            "the webview's render process stopped responding (unresponsive)"
        );
        assert_eq!(
            process_failure(None, None, None),
            "the webview's process failed (no reason given)"
        );
        assert_eq!(
            process_failure(Some(42), Some(9), Some(1)),
            "the webview's process failed (reason 9, exit code 1)"
        );
    }

    #[test]
    fn only_numbers_of_a_failed_process_are_read() {
        // The description of a failed process and the path of the module that failed can name
        // the user (a path in their profile) and are not needed: keep them out of the log.
        let source = include_str!("screen.rs").replace("\r\n", "\n");
        let production = source.split("#[cfg(test)]").next().unwrap();
        for call in [
            "ProcessDescription",
            "FailureSourceModulePath",
            "GetWindowText",
            "GetClassName",
        ] {
            assert!(!production.contains(call), "the watcher calls {call}");
        }
    }

    #[cfg(windows)]
    #[test]
    fn the_numbers_are_the_ones_windows_uses() {
        use windows::Win32::UI::WindowsAndMessaging as wm;

        assert_eq!(WM_SETTINGCHANGE, wm::WM_SETTINGCHANGE);
        assert_eq!(WM_DISPLAYCHANGE, wm::WM_DISPLAYCHANGE);
        assert_eq!(WM_POWERBROADCAST, wm::WM_POWERBROADCAST);
        assert_eq!(WM_WTSSESSION_CHANGE, wm::WM_WTSSESSION_CHANGE);
        assert_eq!(WM_DPICHANGED, wm::WM_DPICHANGED);
        assert_eq!(WM_DWMCOMPOSITIONCHANGED, wm::WM_DWMCOMPOSITIONCHANGED);
        assert_eq!(PBT_APMSUSPEND, wm::PBT_APMSUSPEND as usize);
        assert_eq!(PBT_APMRESUMEAUTOMATIC, wm::PBT_APMRESUMEAUTOMATIC as usize);
        assert_eq!(PBT_POWERSETTINGCHANGE, wm::PBT_POWERSETTINGCHANGE as usize);
        assert_eq!(SPI_SETWORKAREA, wm::SPI_SETWORKAREA.0 as usize);
        assert_eq!(WTS_CONSOLE_CONNECT, wm::WTS_CONSOLE_CONNECT as usize);
        assert_eq!(WTS_CONSOLE_DISCONNECT, wm::WTS_CONSOLE_DISCONNECT as usize);
        assert_eq!(WTS_REMOTE_CONNECT, wm::WTS_REMOTE_CONNECT as usize);
        assert_eq!(WTS_REMOTE_DISCONNECT, wm::WTS_REMOTE_DISCONNECT as usize);
        assert_eq!(WTS_SESSION_LOCK, wm::WTS_SESSION_LOCK as usize);
        assert_eq!(WTS_SESSION_UNLOCK, wm::WTS_SESSION_UNLOCK as usize);
    }

    #[cfg(windows)]
    #[test]
    fn the_numbers_of_failed_processes_are_the_ones_webview2_uses() {
        use webview2_com::Microsoft::Web::WebView2::Win32::*;

        let kinds = [
            (
                COREWEBVIEW2_PROCESS_FAILED_KIND_BROWSER_PROCESS_EXITED,
                "browser process exited",
            ),
            (
                COREWEBVIEW2_PROCESS_FAILED_KIND_RENDER_PROCESS_EXITED,
                "render process exited",
            ),
            (
                COREWEBVIEW2_PROCESS_FAILED_KIND_RENDER_PROCESS_UNRESPONSIVE,
                "render process stopped responding",
            ),
            (
                COREWEBVIEW2_PROCESS_FAILED_KIND_FRAME_RENDER_PROCESS_EXITED,
                "render process of a frame exited",
            ),
            (
                COREWEBVIEW2_PROCESS_FAILED_KIND_UTILITY_PROCESS_EXITED,
                "utility process exited",
            ),
            (
                COREWEBVIEW2_PROCESS_FAILED_KIND_SANDBOX_HELPER_PROCESS_EXITED,
                "sandbox helper process exited",
            ),
            (
                COREWEBVIEW2_PROCESS_FAILED_KIND_GPU_PROCESS_EXITED,
                "GPU process exited",
            ),
            (
                COREWEBVIEW2_PROCESS_FAILED_KIND_PPAPI_PLUGIN_PROCESS_EXITED,
                "plugin process exited",
            ),
            (
                COREWEBVIEW2_PROCESS_FAILED_KIND_PPAPI_BROKER_PROCESS_EXITED,
                "plugin broker process exited",
            ),
            (
                COREWEBVIEW2_PROCESS_FAILED_KIND_UNKNOWN_PROCESS_EXITED,
                "unknown process exited",
            ),
        ];
        for (kind, words) in kinds {
            assert!(
                process_failure(Some(kind.0), None, None).contains(words),
                "{kind:?}"
            );
        }
        let reasons = [
            (
                COREWEBVIEW2_PROCESS_FAILED_REASON_UNEXPECTED,
                "unexpectedly",
            ),
            (
                COREWEBVIEW2_PROCESS_FAILED_REASON_UNRESPONSIVE,
                "unresponsive",
            ),
            (COREWEBVIEW2_PROCESS_FAILED_REASON_TERMINATED, "terminated"),
            (COREWEBVIEW2_PROCESS_FAILED_REASON_CRASHED, "crashed"),
            (
                COREWEBVIEW2_PROCESS_FAILED_REASON_LAUNCH_FAILED,
                "failed to launch",
            ),
            (
                COREWEBVIEW2_PROCESS_FAILED_REASON_OUT_OF_MEMORY,
                "out of memory",
            ),
            (
                COREWEBVIEW2_PROCESS_FAILED_REASON_PROFILE_DELETED,
                "its profile was deleted",
            ),
        ];
        for (reason, words) in reasons {
            assert!(
                process_failure(Some(1), Some(reason.0), None).contains(&format!("({words})")),
                "{reason:?}"
            );
        }
    }
}
