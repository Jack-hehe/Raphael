// Ported from Handy (https://github.com/cjpais/Handy), src-tauri/src/paste_tx/windows.rs and
// src-tauri/src/paste_tx/mod.rs at commit 9e57a0d, under the MIT license:
//
// Copyright (c) 2025 CJ Pais
//
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
//
// The above copyright notice and this permission notice shall be included in all
// copies or substantial portions of the Software.
//
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
// IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
// FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
// AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
// LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
// OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
// SOFTWARE.

//! The clipboard half of pasting.
//!
//! [`ClipboardSession::begin`] snapshots every format on the clipboard.
//! [`ClipboardSession::publish_text`] then empties it and offers the dictated text as a promise
//! (delayed rendering: `SetClipboardData` without data), next to markers that keep it out of
//! clipboard history, the cloud clipboard and clipboard managers. When a program reads the
//! clipboard, Windows asks the session's hidden window for the text (`WM_RENDERFORMAT`); that
//! request is the read receipt. [`ClipboardSession::wait_and_restore`] puts the snapshot back
//! once the reads have stopped, unless the user copied something new meanwhile;
//! [`ClipboardSession::keep`] leaves the text on the clipboard instead.
//!
//! A session belongs to the thread that began it (it is not `Send`): Windows' requests to its
//! window are answered only while that thread is inside a session method that waits. After
//! `publish_text` the first such wait is the paste chord's hold, so every read the session sees
//! comes after the chord.
//!
//! Changes from Handy: `windows` 0.62; the session runs on the calling thread instead of its own;
//! opening the clipboard is retried while another program has it open; the sequence number is
//! read again after each render; `CF_ENHMETAFILE` is kept (as a copy), while `CF_METAFILEPICT`
//! (its handle dies with the original) and the private and GDI-object format ranges are skipped;
//! the restored copy carries the markers too, so it does not enter clipboard history again.

use std::cell::{Cell, RefCell};
use std::marker::PhantomData;
use std::sync::Once;
use std::time::{Duration, Instant};

use windows::Win32::Foundation::{
    ERROR_SUCCESS, HANDLE, HGLOBAL, HINSTANCE, HWND, LPARAM, LRESULT, SetLastError, WPARAM,
};
use windows::Win32::Graphics::Gdi::{CopyEnhMetaFileW, HBITMAP, HENHMETAFILE};
use windows::Win32::System::DataExchange::{
    CloseClipboard, EmptyClipboard, EnumClipboardFormats, GetClipboardData, GetClipboardOwner,
    GetClipboardSequenceNumber, OpenClipboard, RegisterClipboardFormatW, SetClipboardData,
};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::System::Memory::{
    GMEM_MOVEABLE, GlobalAlloc, GlobalLock, GlobalSize, GlobalUnlock,
};
use windows::Win32::System::Ole::{
    CF_BITMAP, CF_DSPBITMAP, CF_DSPENHMETAFILE, CF_DSPMETAFILEPICT, CF_DSPTEXT, CF_ENHMETAFILE,
    CF_GDIOBJLAST, CF_METAFILEPICT, CF_OWNERDISPLAY, CF_PALETTE, CF_PRIVATEFIRST, CF_UNICODETEXT,
};
use windows::Win32::UI::WindowsAndMessaging::{
    CopyImage, CreateWindowExW, DefWindowProcW, DestroyWindow, DispatchMessageW, GWLP_USERDATA,
    GetMessageW, GetWindowLongPtrW, HWND_MESSAGE, IMAGE_BITMAP, KillTimer, LR_CREATEDIBSECTION,
    MSG, PM_REMOVE, PeekMessageW, RegisterClassW, SetTimer, SetWindowLongPtrW, WINDOW_EX_STYLE,
    WINDOW_STYLE, WM_DESTROYCLIPBOARD, WM_RENDERALLFORMATS, WM_RENDERFORMAT, WNDCLASSW,
};
use windows::core::{Free, Owned, PCWSTR, w};

/// How long after the last read of the text the user's clipboard goes back. Some programs read
/// the clipboard more than once per paste.
const QUIET_PERIOD: Duration = Duration::from_millis(200);
/// How long to wait for the target to read the text before restoring anyway.
const READ_TIMEOUT: Duration = Duration::from_secs(8);
/// The same when the paste chord could not be sent.
const NO_CHORD_TIMEOUT: Duration = Duration::from_millis(500);
/// How often a waiting session looks at the time (with a `WM_TIMER`).
const TICK_MS: u32 = 25;
const TIMER_ID: usize = 1;
/// A clipboard format bigger than this is not kept.
const MAX_FORMAT_BYTES: usize = 64 * 1024 * 1024;
/// How many times, and how far apart, to try opening the clipboard while another program has it
/// open.
const OPEN_ATTEMPTS: u32 = 20;
const OPEN_RETRY: Duration = Duration::from_millis(25);
const CLASS_NAME: PCWSTR = w!("RaphaelClipboardSession");

const UNICODETEXT: u32 = CF_UNICODETEXT.0 as u32;
const BITMAP: u32 = CF_BITMAP.0 as u32;
const ENHMETAFILE: u32 = CF_ENHMETAFILE.0 as u32;

/// Registered formats, each holding a `u32`, that keep the clipboard's content out of clipboard
/// history (Win+V), the cloud clipboard and clipboard managers.
const MARKERS: [(PCWSTR, u32); 3] = [
    (w!("ExcludeClipboardContentFromMonitorProcessing"), 1),
    (w!("CanIncludeInClipboardHistory"), 0),
    (w!("CanUploadToCloudClipboard"), 0),
];

/// One paste on the clipboard: snapshot the user's clipboard, offer the text, then restore the
/// snapshot or keep the text.
pub struct ClipboardSession {
    /// The session's hidden message-only window. It owns the clipboard while the text is there.
    hwnd: HWND,
    /// What the window procedure shares with the session. Boxed so that its address stays put:
    /// the window's `GWLP_USERDATA` points to it.
    shared: Box<Shared>,
    /// The user's clipboard, in the order Windows listed its formats.
    snapshot: Vec<Saved>,
}

#[derive(Default)]
struct Shared {
    /// The text, as UTF-16 with a terminating NUL.
    text: RefCell<Vec<u16>>,
    /// When the text went on the clipboard (`None` before that).
    published_at: Cell<Option<Instant>>,
    /// The clipboard's sequence number while it still holds our text.
    sequence: Cell<u32>,
    /// When a program last asked for the text.
    last_read: Cell<Option<Instant>>,
    /// The clipboard holds our promise of the text, not rendered yet.
    pending: Cell<bool>,
    /// Someone else emptied the clipboard (`WM_DESTROYCLIPBOARD`): the user copied something new.
    lost: Cell<bool>,
}

/// One format of the user's clipboard.
enum Saved {
    /// Global memory, kept as a copy of its bytes.
    Bytes { format: u32, data: Vec<u8> },
    /// `CF_BITMAP`, a GDI bitmap, kept as a copy.
    Bitmap(Owned<HBITMAP>),
    /// `CF_ENHMETAFILE`, a GDI metafile, kept as a copy.
    EnhMetafile(Owned<HENHMETAFILE>),
}

impl ClipboardSession {
    /// Creates the session's window and snapshots every format on the clipboard.
    pub fn begin() -> Result<Self, String> {
        let hwnd = create_window()?;
        let shared = Box::<Shared>::default();
        // SAFETY: `shared` belongs to the session, whose `Drop` destroys the window before
        // `shared` is freed.
        unsafe { SetWindowLongPtrW(hwnd, GWLP_USERDATA, &raw const *shared as isize) };
        let mut session = Self {
            hwnd,
            shared,
            snapshot: Vec::new(),
        };
        let _opened = session.open()?;
        // SAFETY: the clipboard is open.
        session.snapshot = unsafe { snapshot() };
        Ok(session)
    }

    /// Empties the clipboard and offers `text` on it, marked to stay out of clipboard history,
    /// the cloud clipboard and clipboard managers. The window hands the text over when a
    /// program reads it.
    pub fn publish_text(&mut self, text: &str) -> Result<(), String> {
        *self.shared.text.borrow_mut() = text.encode_utf16().chain([0]).collect();
        let offered = {
            let _opened = self.open()?;
            // SAFETY: the clipboard is open, by our window.
            unsafe { offer_text() }
        };
        if let Err(e) = offered {
            // The clipboard may be empty by now: give the user's things back.
            if let Err(restore) = self.put_back(false) {
                log::warn!("paste: could not put the clipboard back: {restore}");
            }
            return Err(e);
        }
        // Read after closing the clipboard, as Handy does.
        self.shared
            .sequence
            .set(unsafe { GetClipboardSequenceNumber() });
        self.shared.published_at.set(Some(Instant::now()));
        self.shared.last_read.set(None);
        self.shared.pending.set(true);
        self.shared.lost.set(false);
        Ok(())
    }

    /// Waits until the reads of the text have stopped for [`QUIET_PERIOD`] (or until
    /// [`READ_TIMEOUT`], [`NO_CHORD_TIMEOUT`] if `chord_sent` is false, passes without one), then
    /// puts the user's clipboard back, unless the clipboard has changed since: then the user
    /// copied something new, and that stays.
    pub fn wait_and_restore(mut self, chord_sent: bool) {
        let Some(published_at) = self.shared.published_at.get() else {
            return;
        };
        let shared = &*self.shared;
        self.pump_until(|| {
            shared.lost.get()
                || restore_due(
                    Instant::now(),
                    published_at,
                    shared.last_read.get(),
                    chord_sent,
                )
        });
        let read = match self.shared.last_read.get() {
            Some(at) => {
                let after = at.saturating_duration_since(published_at).as_millis();
                format!("read {after} ms after it was offered")
            }
            None => "not read".to_owned(),
        };
        let restored = if self.shared.lost.get() {
            Ok(false)
        } else {
            self.put_back(true)
        };
        match restored {
            Ok(true) => log::info!("paste: text {read}; clipboard restored"),
            Ok(false) => log::info!("paste: text {read}; the clipboard changed since, left as is"),
            Err(e) => log::warn!("paste: text {read}; could not restore the clipboard: {e}"),
        }
    }

    /// Leaves the text on the clipboard (still kept out of clipboard history) and drops the
    /// snapshot.
    pub fn keep(self) {
        if self.shared.published_at.get().is_none() {
            return;
        }
        match self.open() {
            // SAFETY: the clipboard is open, by our window.
            Ok(_opened) => unsafe { render_pending(self.hwnd, &self.shared) },
            // Destroying the window still renders the text (WM_RENDERALLFORMATS).
            Err(e) => log::warn!("paste: {e}; the text stays as a promise until the window goes"),
        }
    }

    /// Answers the messages for this thread's windows during `duration`.
    pub(super) fn pump_for(&self, duration: Duration) {
        let until = Instant::now() + duration;
        self.pump_until(|| Instant::now() >= until);
    }

    /// Answers the messages for this thread's windows until `done()`, which is checked after
    /// each message and at least every [`TICK_MS`].
    fn pump_until(&self, mut done: impl FnMut() -> bool) {
        // SAFETY: calls on our own window, from the thread that created it.
        unsafe {
            let timer = SetTimer(Some(self.hwnd), TIMER_ID, TICK_MS, None);
            let mut msg = MSG::default();
            while !done() {
                if timer == 0 {
                    // No timer (out of resources): poll, rather than wait for a message that
                    // may never come.
                    while PeekMessageW(&mut msg, Some(self.hwnd), 0, 0, PM_REMOVE).as_bool() {
                        DispatchMessageW(&msg);
                    }
                    std::thread::sleep(Duration::from_millis(TICK_MS.into()));
                    continue;
                }
                // Requests for the text are sent messages: GetMessageW passes them to the window
                // procedure while it waits, and returns for the next WM_TIMER.
                if GetMessageW(&mut msg, Some(self.hwnd), 0, 0).0 <= 0 {
                    break;
                }
                DispatchMessageW(&msg);
            }
            if timer != 0 {
                let _ = KillTimer(Some(self.hwnd), TIMER_ID);
            }
        }
    }

    /// Opens the clipboard for our window, retrying while another program has it open. Between
    /// tries it answers our window's messages: that program may be waiting for our text.
    fn open(&self) -> Result<Opened, String> {
        let mut error = String::new();
        for attempt in 0..OPEN_ATTEMPTS {
            if attempt > 0 {
                self.pump_for(OPEN_RETRY);
            }
            match unsafe { OpenClipboard(Some(self.hwnd)) } {
                Ok(()) => return Ok(Opened(PhantomData)),
                Err(e) => error = e.to_string(),
            }
        }
        Err(format!("could not open the clipboard: {error}"))
    }

    /// Puts the snapshot back on the clipboard. With `only_if_ours`, leaves the clipboard alone
    /// (and returns `false`) if it no longer holds our text; that is checked with the clipboard
    /// open, so nobody can copy something between the check and the restore.
    fn put_back(&mut self, only_if_ours: bool) -> Result<bool, String> {
        let _opened = self.open()?;
        if only_if_ours && !self.still_ours() {
            return Ok(false);
        }
        // SAFETY: the clipboard is open, by our window.
        unsafe {
            EmptyClipboard().map_err(|e| format!("could not empty the clipboard: {e}"))?;
            // Our promise, if still open, went with the old contents.
            self.shared.pending.set(false);
            let formats: Vec<u32> = self.snapshot.iter().map(Saved::format).collect();
            for saved in self.snapshot.drain(..) {
                saved.put();
            }
            // The user's own copy is in clipboard history already, if it may be there at all;
            // marked, the restored copy is not added again after every dictation.
            if !formats.is_empty()
                && let Err(e) = put_markers(|format| formats.contains(&format))
            {
                log::warn!("paste: the restored clipboard is not marked: {e}");
            }
        }
        Ok(true)
    }

    /// Whether the clipboard, which must be open, still holds the text we published.
    fn still_ours(&self) -> bool {
        // SAFETY: plain queries.
        unsafe {
            !self.shared.lost.get()
                && GetClipboardSequenceNumber() == self.shared.sequence.get()
                && GetClipboardOwner().is_ok_and(|owner| owner == self.hwnd)
        }
    }
}

impl Drop for ClipboardSession {
    fn drop(&mut self) {
        // If the text is still only promised, Windows now has the window render it
        // (WM_RENDERALLFORMATS), so it stays on the clipboard once the window is gone.
        // SAFETY: our own window, destroyed by the thread that created it. `shared` is freed
        // only after this; should the window outlive it, it no longer points there.
        unsafe {
            if DestroyWindow(self.hwnd).is_err() {
                SetWindowLongPtrW(self.hwnd, GWLP_USERDATA, 0);
            }
        }
    }
}

/// Whether to put the user's clipboard back now: [`QUIET_PERIOD`] after the last read of the
/// text, or once waiting for a read has taken too long.
fn restore_due(
    now: Instant,
    published_at: Instant,
    last_read: Option<Instant>,
    chord_sent: bool,
) -> bool {
    let quiet = last_read.is_some_and(|read| now.saturating_duration_since(read) >= QUIET_PERIOD);
    let limit = if chord_sent {
        READ_TIMEOUT
    } else {
        NO_CHORD_TIMEOUT
    };
    quiet || now.saturating_duration_since(published_at) >= limit
}

/// The clipboard is open, by this thread, while this lives.
struct Opened(PhantomData<*const ()>);

impl Drop for Opened {
    fn drop(&mut self) {
        // SAFETY: opened by this thread (`Opened` is not `Send`).
        unsafe {
            let _ = CloseClipboard();
        }
    }
}

/// Creates a hidden message-only (`HWND_MESSAGE`) window for a session.
fn create_window() -> Result<HWND, String> {
    static REGISTER: Once = Once::new();
    // SAFETY: plain calls; the window class lives as long as the process.
    unsafe {
        let instance: HINSTANCE = GetModuleHandleW(PCWSTR::null())
            .map_err(|e| format!("could not get the module handle: {e}"))?
            .into();
        REGISTER.call_once(|| {
            let class = WNDCLASSW {
                lpfnWndProc: Some(window_proc),
                hInstance: instance,
                lpszClassName: CLASS_NAME,
                ..Default::default()
            };
            // If this fails, CreateWindowExW below fails and says why.
            RegisterClassW(&class);
        });
        CreateWindowExW(
            WINDOW_EX_STYLE::default(),
            CLASS_NAME,
            PCWSTR::null(),
            WINDOW_STYLE::default(),
            0,
            0,
            0,
            0,
            Some(HWND_MESSAGE),
            None,
            Some(instance),
            None,
        )
        .map_err(|e| format!("could not create the clipboard window: {e}"))
    }
}

unsafe extern "system" fn window_proc(
    hwnd: HWND,
    msg: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    // SAFETY: `begin` points GWLP_USERDATA at the session's `Shared`, which outlives the window
    // (until then it is null). Only shared references are made to it, and its fields are cells.
    let shared = unsafe { (GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *const Shared).as_ref() };
    match (msg, shared) {
        (WM_RENDERFORMAT, Some(shared)) => {
            // A program reads the clipboard: Windows has opened it for that program and asks us
            // for the text we promised. This is the read receipt.
            shared.last_read.set(Some(Instant::now()));
            if wparam.0 == UNICODETEXT as usize {
                // SAFETY: the clipboard is open (for the reader), as WM_RENDERFORMAT requires.
                unsafe { render(shared) };
                shared.pending.set(false);
                // Rendering leaves the sequence number as it is (seen on Windows 11), but should
                // that ever change: the reader holds the clipboard open, so this is still ours.
                shared.sequence.set(unsafe { GetClipboardSequenceNumber() });
            }
            LRESULT(0)
        }
        (WM_RENDERALLFORMATS, Some(shared)) => {
            // Sent when the window is destroyed while it owns the clipboard, also after a restore
            // made it the owner of the user's things: render only a promise that is still open.
            // Unlike for WM_RENDERFORMAT, Windows does not open the clipboard for us here.
            if shared.pending.get() && unsafe { OpenClipboard(Some(hwnd)) }.is_ok() {
                let _opened = Opened(PhantomData);
                // SAFETY: the clipboard is open, by our window.
                unsafe { render_pending(hwnd, shared) };
            }
            LRESULT(0)
        }
        (WM_DESTROYCLIPBOARD, Some(shared)) => {
            shared.pending.set(false);
            shared.lost.set(true);
            LRESULT(0)
        }
        _ => unsafe { DefWindowProcW(hwnd, msg, wparam, lparam) },
    }
}

/// Puts the text on the clipboard, which must be open, for real.
unsafe fn render(shared: &Shared) {
    // Never borrowed mutably while Windows can call the window procedure.
    let Ok(text) = shared.text.try_borrow() else {
        return;
    };
    let rendered = unsafe { global_from(text.as_slice()) }
        .is_some_and(|block| unsafe { give(UNICODETEXT, block) });
    if !rendered {
        log::warn!("paste: could not hand the text to the program reading the clipboard");
    }
}

/// Renders the text if it is still only promised on the clipboard, which must be open.
unsafe fn render_pending(hwnd: HWND, shared: &Shared) {
    if shared.pending.get() && unsafe { GetClipboardOwner() }.is_ok_and(|owner| owner == hwnd) {
        unsafe { render(shared) };
    }
    shared.pending.set(false);
}

/// Empties the clipboard, which must be open by our window (that makes the window its owner),
/// and offers the text with the markers.
unsafe fn offer_text() -> Result<(), String> {
    unsafe { EmptyClipboard() }.map_err(|e| format!("could not empty the clipboard: {e}"))?;
    unsafe { put_markers(|_| false) }?;
    // No data means delayed rendering. SetClipboardData returns the handle it was given, here
    // null, which the windows crate reports as an error carrying GetLastError(): only a nonzero
    // error is a failure, so the thread's last error is cleared first.
    unsafe { SetLastError(ERROR_SUCCESS) };
    match unsafe { SetClipboardData(UNICODETEXT, None) } {
        Err(e) if e.code().is_err() => Err(format!("could not offer the text: {e}")),
        _ => Ok(()),
    }
}

/// Adds the [`MARKERS`] to the clipboard, which must be open, except those for which
/// `present(format)` says the clipboard has them already.
unsafe fn put_markers(present: impl Fn(u32) -> bool) -> Result<(), String> {
    for (name, value) in MARKERS {
        let format = unsafe { RegisterClipboardFormatW(name) };
        if format == 0 {
            return Err("could not register a marker format".to_owned());
        }
        if present(format) {
            continue;
        }
        let block = unsafe { global_from(std::slice::from_ref(&value)) }.ok_or("out of memory")?;
        if !unsafe { give(format, block) } {
            return Err(format!("could not set marker format {format}"));
        }
    }
    Ok(())
}

/// Copies every format on the clipboard, which must be open, that can be copied.
unsafe fn snapshot() -> Vec<Saved> {
    let mut saved = Vec::new();
    let mut format = 0;
    loop {
        format = unsafe { EnumClipboardFormats(format) };
        if format == 0 {
            break;
        }
        let kind = kind(format);
        if kind == Kind::Skip {
            continue;
        }
        // Asking for a format makes its owner render it, if it was only promised.
        let Ok(handle) = (unsafe { GetClipboardData(format) }) else {
            continue;
        };
        match kind {
            Kind::Bytes => {
                let size = unsafe { GlobalSize(HGLOBAL(handle.0)) };
                if size > MAX_FORMAT_BYTES {
                    log::warn!("paste: clipboard format {format} has {size} bytes; not kept");
                } else if let Some(data) = unsafe { copy_global(HGLOBAL(handle.0)) } {
                    saved.push(Saved::Bytes { format, data });
                }
            }
            Kind::Bitmap => {
                // A GDI object, not memory: keep a copy of the bitmap.
                if let Ok(copy) =
                    unsafe { CopyImage(handle, IMAGE_BITMAP, 0, 0, LR_CREATEDIBSECTION) }
                {
                    saved.push(Saved::Bitmap(unsafe { Owned::new(HBITMAP(copy.0)) }));
                }
            }
            Kind::EnhMetafile => {
                let copy = unsafe { CopyEnhMetaFileW(HENHMETAFILE(handle.0), PCWSTR::null()) };
                if !copy.is_invalid() {
                    saved.push(Saved::EnhMetafile(unsafe { Owned::new(copy) }));
                }
            }
            Kind::Skip => {}
        }
    }
    saved
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Kind {
    Bytes,
    Bitmap,
    EnhMetafile,
    Skip,
}

/// How a clipboard format can be kept.
fn kind(format: u32) -> Kind {
    const METAFILEPICT: u32 = CF_METAFILEPICT.0 as u32;
    const PALETTE: u32 = CF_PALETTE.0 as u32;
    const OWNERDISPLAY: u32 = CF_OWNERDISPLAY.0 as u32;
    const DSPTEXT: u32 = CF_DSPTEXT.0 as u32;
    const DSPBITMAP: u32 = CF_DSPBITMAP.0 as u32;
    const DSPMETAFILEPICT: u32 = CF_DSPMETAFILEPICT.0 as u32;
    const DSPENHMETAFILE: u32 = CF_DSPENHMETAFILE.0 as u32;
    const PRIVATEFIRST: u32 = CF_PRIVATEFIRST.0 as u32;
    const GDIOBJLAST: u32 = CF_GDIOBJLAST.0 as u32;
    match format {
        BITMAP => Kind::Bitmap,
        ENHMETAFILE => Kind::EnhMetafile,
        // Handles that are not plain memory, and CF_METAFILEPICT, whose memory holds a metafile
        // handle that dies with the original (Windows makes CF_METAFILEPICT from
        // CF_ENHMETAFILE again when a program asks for it).
        METAFILEPICT | PALETTE | OWNERDISPLAY | DSPTEXT | DSPBITMAP | DSPMETAFILEPICT
        | DSPENHMETAFILE => Kind::Skip,
        // Private formats, whose data Windows does not manage, and GDI objects.
        PRIVATEFIRST..=GDIOBJLAST => Kind::Skip,
        _ => Kind::Bytes,
    }
}

impl Saved {
    fn format(&self) -> u32 {
        match self {
            Saved::Bytes { format, .. } => *format,
            Saved::Bitmap(_) => BITMAP,
            Saved::EnhMetafile(_) => ENHMETAFILE,
        }
    }

    /// Puts this format back on the clipboard, which must be open.
    unsafe fn put(self) {
        let format = self.format();
        let given = match self {
            Saved::Bytes { data, .. } => unsafe { global_from(data.as_slice()) }
                .is_some_and(|block| unsafe { give(format, block) }),
            Saved::Bitmap(bitmap) => unsafe { give(format, bitmap) },
            Saved::EnhMetafile(metafile) => unsafe { give(format, metafile) },
        };
        if !given {
            log::warn!("paste: could not put clipboard format {format} back");
        }
    }
}

/// Handle types the clipboard can take over.
trait ClipboardData: Free + Copy {
    fn handle(self) -> HANDLE;
}

impl ClipboardData for HGLOBAL {
    fn handle(self) -> HANDLE {
        HANDLE(self.0)
    }
}

impl ClipboardData for HBITMAP {
    fn handle(self) -> HANDLE {
        HANDLE(self.0)
    }
}

impl ClipboardData for HENHMETAFILE {
    fn handle(self) -> HANDLE {
        HANDLE(self.0)
    }
}

/// Puts `data` on the clipboard, which must be open, as `format`. From then on the clipboard
/// owns it; if Windows refuses it, it is freed as it drops.
unsafe fn give<T: ClipboardData>(format: u32, data: Owned<T>) -> bool {
    if unsafe { SetClipboardData(format, Some((*data).handle())) }.is_err() {
        return false;
    }
    std::mem::forget(data);
    true
}

/// A new movable global memory block (the kind the clipboard takes) holding a copy of `items`.
unsafe fn global_from<T: Copy>(items: &[T]) -> Option<Owned<HGLOBAL>> {
    let size = size_of_val(items);
    // SAFETY: the block is ours until `give` hands it over; `Owned` frees it otherwise.
    let block = unsafe { Owned::new(GlobalAlloc(GMEM_MOVEABLE, size).ok()?) };
    let ptr = unsafe { GlobalLock(*block) }.cast::<u8>();
    if ptr.is_null() {
        return None;
    }
    // SAFETY: the locked block has at least `size` bytes and is not `items`.
    unsafe { std::ptr::copy_nonoverlapping(items.as_ptr().cast::<u8>(), ptr, size) };
    // GlobalUnlock reports an error (NO_ERROR) once the lock count is back to zero.
    let _ = unsafe { GlobalUnlock(*block) };
    Some(block)
}

/// A copy of the bytes in a global memory block (`None` if it is empty or cannot be locked).
unsafe fn copy_global(block: HGLOBAL) -> Option<Vec<u8>> {
    let size = unsafe { GlobalSize(block) };
    if size == 0 {
        return None;
    }
    let ptr = unsafe { GlobalLock(block) }.cast::<u8>().cast_const();
    if ptr.is_null() {
        return None;
    }
    // SAFETY: the block is locked and has `size` bytes.
    let data = unsafe { std::slice::from_raw_parts(ptr, size) }.to_vec();
    let _ = unsafe { GlobalUnlock(block) };
    Some(data)
}

#[cfg(test)]
mod tests {
    use super::*;
    use windows::Win32::System::DataExchange::IsClipboardFormatAvailable;
    use windows::Win32::System::Ole::{CF_DIB, CF_HDROP};

    fn ms(n: u64) -> Duration {
        Duration::from_millis(n)
    }

    #[test]
    fn waits_for_a_read_until_the_timeout() {
        let t0 = Instant::now();
        assert!(!restore_due(t0 + ms(7_999), t0, None, true));
        assert!(restore_due(t0 + ms(8_000), t0, None, true));
    }

    #[test]
    fn restores_200_ms_after_the_last_read() {
        let t0 = Instant::now();
        let read = Some(t0 + ms(300));
        assert!(!restore_due(t0 + ms(499), t0, read, true));
        assert!(restore_due(t0 + ms(500), t0, read, true));
    }

    #[test]
    fn gives_up_after_500_ms_without_a_chord() {
        let t0 = Instant::now();
        assert!(!restore_due(t0 + ms(499), t0, None, false));
        assert!(restore_due(t0 + ms(500), t0, None, false));
    }

    // The real clipboard from here on (Review Focus 3).

    /// Puts back the clipboard it snapshotted when dropped, so the test leaves the user's
    /// clipboard as it found it even when an assertion fails.
    struct PutBack(ClipboardSession);

    impl Drop for PutBack {
        fn drop(&mut self) {
            if let Err(e) = self.0.put_back(false) {
                eprintln!("could not put the clipboard back: {e}");
            }
        }
    }

    /// A 2x2 32-bit `CF_DIB`: a BITMAPINFOHEADER and four BGRA pixels.
    fn dib_2x2() -> Vec<u8> {
        let mut dib = Vec::new();
        for field in [40u32, 2, 2] {
            dib.extend(field.to_le_bytes()); // biSize, biWidth, biHeight
        }
        dib.extend(1u16.to_le_bytes()); // biPlanes
        dib.extend(32u16.to_le_bytes()); // biBitCount
        for field in [0u32, 16, 0, 0, 0, 0] {
            dib.extend(field.to_le_bytes()); // biCompression = BI_RGB, biSizeImage, the rest 0
        }
        dib.extend([0, 0, 255, 0, 0, 255, 0, 0, 255, 0, 0, 0, 255, 255, 255, 0]);
        dib
    }

    /// A `CF_HDROP` (a DROPFILES header and a double-NUL-terminated UTF-16 list) of `files`.
    fn hdrop(files: &[&str]) -> Vec<u8> {
        let mut data = Vec::new();
        for field in [20u32, 0, 0, 0, 1] {
            data.extend(field.to_le_bytes()); // pFiles, pt.x, pt.y, fNC, fWide
        }
        for file in files {
            data.extend(file.encode_utf16().chain([0]).flat_map(u16::to_le_bytes));
        }
        data.extend([0, 0]);
        data
    }

    /// The file list in a `CF_HDROP`.
    fn files_in(hdrop: &[u8]) -> Vec<String> {
        let start = u32::from_le_bytes(hdrop[0..4].try_into().unwrap()) as usize;
        let units: Vec<u16> = hdrop[start..]
            .as_chunks()
            .0
            .iter()
            .map(|&pair| u16::from_le_bytes(pair))
            .collect();
        units
            .split(|&unit| unit == 0)
            .take_while(|name| !name.is_empty())
            .map(|name| String::from_utf16(name).unwrap())
            .collect()
    }

    /// The bytes of one clipboard format, opening the clipboard through `opener`.
    fn read(opener: &ClipboardSession, format: u32) -> Option<Vec<u8>> {
        let _opened = opener.open().expect("open the clipboard");
        let handle = unsafe { GetClipboardData(format) }.ok()?;
        unsafe { copy_global(HGLOBAL(handle.0)) }
    }

    fn read_text(opener: &ClipboardSession) -> Option<String> {
        let bytes = read(opener, UNICODETEXT)?;
        let units: Vec<u16> = bytes
            .as_chunks()
            .0
            .iter()
            .map(|&pair| u16::from_le_bytes(pair))
            .take_while(|&unit| unit != 0)
            .collect();
        String::from_utf16(&units).ok()
    }

    fn read_u32(opener: &ClipboardSession, format: u32) -> Option<u32> {
        Some(u32::from_ne_bytes(
            read(opener, format)?.get(..4)?.try_into().ok()?,
        ))
    }

    fn available(format: u32) -> bool {
        unsafe { IsClipboardFormatAvailable(format) }.is_ok()
    }

    fn registered(name: PCWSTR) -> u32 {
        unsafe { RegisterClipboardFormatW(name) }
    }

    #[test]
    #[ignore = "uses the real clipboard (and puts back what was on it)"]
    fn restore_keeps_image_and_files() {
        let users = PutBack(ClipboardSession::begin().expect("snapshot the clipboard"));
        let opener = &users.0;

        // Put a 2x2 CF_DIB image and a CF_HDROP file list on the clipboard.
        let dib = dib_2x2();
        let files = ["C:\\Raphael\\notes.txt", "C:\\Raphael\\測試.txt"];
        let file_list = hdrop(&files);
        {
            let _opened = opener.open().expect("open the clipboard");
            unsafe { EmptyClipboard() }.expect("empty the clipboard");
            for (format, bytes) in [(CF_DIB, dib.as_slice()), (CF_HDROP, file_list.as_slice())] {
                let data = unsafe { global_from(bytes) }.expect("allocate");
                assert!(unsafe { give(format.0.into(), data) }, "set {format:?}");
            }
        }
        let dib_before = read(opener, CF_DIB.0.into()).expect("CF_DIB is on the clipboard");
        let hdrop_before = read(opener, CF_HDROP.0.into()).expect("CF_HDROP is on the clipboard");
        assert!(dib_before.starts_with(&dib));
        assert_eq!(files_in(&hdrop_before), files);

        let mut session = ClipboardSession::begin().expect("begin");
        session.publish_text("測試").expect("publish the text");
        // Reading it is what the target does after the chord: Windows asks the session for it.
        assert_eq!(read_text(opener).as_deref(), Some("測試"));
        for (name, value) in MARKERS {
            assert_eq!(
                read_u32(opener, registered(name)),
                Some(value),
                "history marker"
            );
        }

        let waiting = Instant::now();
        session.wait_and_restore(true);
        assert!(
            waiting.elapsed() < Duration::from_secs(2),
            "restored after the read, not at 8 s"
        );

        assert_eq!(read(opener, CF_DIB.0.into()), Some(dib_before));
        let hdrop_after = read(opener, CF_HDROP.0.into()).expect("CF_HDROP is back");
        assert_eq!(files_in(&hdrop_after), files);
        assert!(!available(UNICODETEXT), "the dictated text is gone");
        // The restored copy is marked too, so it does not enter clipboard history a second time.
        let history = registered(w!("CanIncludeInClipboardHistory"));
        assert_eq!(read_u32(opener, history), Some(0));
    }

    #[test]
    #[ignore = "uses the real clipboard (and puts back what was on it)"]
    fn keep_leaves_the_text_on_the_clipboard() {
        let users = PutBack(ClipboardSession::begin().expect("snapshot the clipboard"));
        let opener = &users.0;

        let mut session = ClipboardSession::begin().expect("begin");
        session.publish_text("測試").expect("publish the text");
        session.keep();

        // The session and its window are gone; the text stays, and stays out of history.
        assert_eq!(read_text(opener).as_deref(), Some("測試"));
        for (name, value) in MARKERS {
            assert_eq!(
                read_u32(opener, registered(name)),
                Some(value),
                "history marker"
            );
        }
    }
}
