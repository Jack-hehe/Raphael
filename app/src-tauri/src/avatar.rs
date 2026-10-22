//! Raphael's picture: the emblem by default, or a picture of the person's own (Settings › 拉斐爾 ›
//! 頭像), which then replaces the emblem in the main window and on the overlay.
//!
//! - `avatar_pick` opens the file dialog of Windows (PNG, JPG, WebP or GIF) from here, reads the
//!   file the person chose and keeps it in memory until it is saved or another is picked. The page
//!   gets the picture back as a data URL for its crop dialog, never the path: the page has no way
//!   to name a file of this PC.
//! - `avatar_save {crop}` writes the picture last picked to `%LOCALAPPDATA%\Raphael\avatar\` (this
//!   PC only), with the crop the person chose ([`Crop`]); the file may move or go afterwards, and
//!   Raphael's picture stays. The bytes are kept as they are, so a moving GIF or WebP keeps moving.
//! - `avatar_get` gives the picture and its crop as a data URL, to the main window and to the
//!   overlay; `avatar_clear` deletes it, and Raphael is his emblem again.
//!
//! The overlay is told when the picture changes ([`crate::overlay::Overlay::avatar_changed`]) and
//! asks for it again. Nothing here logs a path, a file name or the bytes: only sizes and kinds.

use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager, Webview};

use crate::rpc::lock;

/// The largest picture that is taken, in bytes.
pub const MAX_BYTES: u64 = 16 * 1024 * 1024;
/// The most a picture may be enlarged in its circle (the crop dialog's own limit).
pub const ZOOM_MAX: f64 = 4.0;
/// The folder of the picture, in the local data folder.
const DIR: &str = "avatar";
/// The picture's bytes.
const PICTURE: &str = "picture";
/// Its kind and its crop.
const META: &str = "picture.json";

/// "只能用 PNG、JPG、WebP 或 GIF 圖片" in the words of the window (backend.ts translates it).
const BAD_KIND: &str = "只能用 PNG、JPG、WebP 或 GIF 圖片";
const TOO_LARGE: &str = "圖片太大了，最多 16 MB";
const NOTHING_PICKED: &str = "還沒有選圖片";

/// Which part of the picture shows in the circle: the picture covers the circle (its shorter side
/// fills it) and is then enlarged `zoom` times about the point at `x`, `y` (fractions of its width
/// and height), which stays at the same fractions of the circle. avatar.ts draws it with CSS, the
/// overlay on its canvas.
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
pub struct Crop {
    pub x: f64,
    pub y: f64,
    pub zoom: f64,
}

impl Crop {
    /// The crop if every number is in range: `x` and `y` from 0 to 1, `zoom` from 1 to [`ZOOM_MAX`].
    pub fn checked(self) -> Result<Self, String> {
        let fine = (0.0..=1.0).contains(&self.x)
            && (0.0..=1.0).contains(&self.y)
            && (1.0..=ZOOM_MAX).contains(&self.zoom);
        if fine {
            Ok(self)
        } else {
            Err("the crop of the picture is out of range".to_owned())
        }
    }
}

/// The picture as the pages get it.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Picture {
    /// `data:image/<kind>;base64,...`
    pub url: String,
    pub crop: Crop,
}

/// What is written beside the bytes.
#[derive(Debug, Serialize, Deserialize)]
struct Meta {
    mime: String,
    crop: Crop,
}

/// The kind of picture `bytes` are, from their first bytes (never from a file name): one of the
/// four the app takes, or `None`.
pub fn kind_of(bytes: &[u8]) -> Option<&'static str> {
    if bytes.starts_with(b"\x89PNG\r\n\x1a\n") {
        Some("image/png")
    } else if bytes.starts_with(&[0xff, 0xd8, 0xff]) {
        Some("image/jpeg")
    } else if bytes.starts_with(b"GIF87a") || bytes.starts_with(b"GIF89a") {
        Some("image/gif")
    } else if bytes.len() >= 12 && &bytes[..4] == b"RIFF" && &bytes[8..12] == b"WEBP" {
        Some("image/webp")
    } else {
        None
    }
}

/// Standard base64 with padding.
pub fn base64(bytes: &[u8]) -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let b = [
            chunk[0],
            chunk.get(1).copied().unwrap_or(0),
            chunk.get(2).copied().unwrap_or(0),
        ];
        let n = (u32::from(b[0]) << 16) | (u32::from(b[1]) << 8) | u32::from(b[2]);
        for (i, shift) in [18, 12, 6, 0].into_iter().enumerate() {
            if i <= chunk.len() {
                out.push(char::from(ALPHABET[((n >> shift) & 63) as usize]));
            } else {
                out.push('=');
            }
        }
    }
    out
}

/// The data URL of a picture of kind `mime`.
pub fn data_url(mime: &str, bytes: &[u8]) -> String {
    format!("data:{mime};base64,{}", base64(bytes))
}

/// A picture read and checked, waiting to be saved.
#[derive(Clone)]
struct Picked {
    mime: &'static str,
    bytes: Vec<u8>,
}

/// Reads a picture of at most [`MAX_BYTES`] and checks that it is one of the four kinds.
fn read_picture(path: &Path) -> Result<Picked, String> {
    let file = fs::File::open(path).map_err(|err| format!("無法讀取圖片：{err}"))?;
    let size = file
        .metadata()
        .map_err(|err| format!("無法讀取圖片：{err}"))?
        .len();
    if size > MAX_BYTES {
        return Err(TOO_LARGE.to_owned());
    }
    let mut bytes = Vec::with_capacity(usize::try_from(size).unwrap_or(0));
    // Read one byte past the limit: a file that grew since its size was read is caught too.
    file.take(MAX_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(|err| format!("無法讀取圖片：{err}"))?;
    if bytes.len() as u64 > MAX_BYTES {
        return Err(TOO_LARGE.to_owned());
    }
    let mime = kind_of(&bytes).ok_or(BAD_KIND)?;
    Ok(Picked { mime, bytes })
}

/// Where the picture is kept, and the one picked and not saved yet. Kept in the app state.
pub struct AvatarStore {
    dir: PathBuf,
    picked: Mutex<Option<Picked>>,
}

impl AvatarStore {
    /// The store in `local` (`%LOCALAPPDATA%\Raphael`): the picture goes in its `avatar` folder.
    pub fn new(local: &Path) -> Self {
        Self {
            dir: local.join(DIR),
            picked: Mutex::new(None),
        }
    }

    /// Reads the picture at `path`, which the file dialog gave; keeps it until it is saved, and
    /// returns it as a data URL for the crop dialog.
    pub fn pick(&self, path: &Path) -> Result<String, String> {
        let picked = read_picture(path)?;
        log::info!(
            "avatar: a picture was picked ({}, {} bytes)",
            picked.mime,
            picked.bytes.len()
        );
        let url = data_url(picked.mime, &picked.bytes);
        *lock(&self.picked) = Some(picked);
        Ok(url)
    }

    /// Writes the picture last picked with `crop`. The bytes go to a temporary file first, which
    /// then replaces the old one, so a picture is never half written.
    pub fn save(&self, crop: Crop) -> Result<(), String> {
        let crop = crop.checked()?;
        let picked = lock(&self.picked).clone().ok_or(NOTHING_PICKED)?;
        let saving = |err: std::io::Error| format!("無法儲存頭像：{err}");
        fs::create_dir_all(&self.dir).map_err(saving)?;
        let meta = serde_json::to_vec(&Meta {
            mime: picked.mime.to_owned(),
            crop,
        })
        .map_err(|err| format!("無法儲存頭像：{err}"))?;
        for (name, bytes) in [(PICTURE, picked.bytes.as_slice()), (META, meta.as_slice())] {
            let temporary = self.dir.join(format!("{name}.tmp"));
            fs::write(&temporary, bytes).map_err(saving)?;
            fs::rename(&temporary, self.dir.join(name)).map_err(saving)?;
        }
        *lock(&self.picked) = None;
        log::info!(
            "avatar: saved ({}, {} bytes)",
            picked.mime,
            picked.bytes.len()
        );
        Ok(())
    }

    /// The picture and its crop, or `None` while Raphael is his emblem (or what is kept cannot be
    /// read, or is not a picture of the four kinds any more).
    pub fn load(&self) -> Option<Picture> {
        let meta: Meta = serde_json::from_slice(&fs::read(self.dir.join(META)).ok()?).ok()?;
        let crop = meta.crop.checked().ok()?;
        let bytes = fs::read(self.dir.join(PICTURE)).ok()?;
        if bytes.len() as u64 > MAX_BYTES {
            return None;
        }
        let mime = kind_of(&bytes)?;
        Some(Picture {
            url: data_url(mime, &bytes),
            crop,
        })
    }

    /// Deletes the picture: Raphael is his emblem again. Nothing to delete is no failure.
    pub fn clear(&self) -> Result<(), String> {
        *lock(&self.picked) = None;
        for name in [META, PICTURE] {
            match fs::remove_file(self.dir.join(name)) {
                Ok(()) => {}
                Err(err) if err.kind() == std::io::ErrorKind::NotFound => {}
                Err(err) => return Err(format!("無法儲存頭像：{err}")),
            }
        }
        log::info!("avatar: back to the emblem");
        Ok(())
    }
}

/// The store, from the app state.
fn store(app: &AppHandle) -> Result<Arc<AvatarStore>, String> {
    app.try_state::<Arc<AvatarStore>>()
        .map(|store| store.inner().clone())
        .ok_or_else(|| "the picture cannot be kept on this PC".to_owned())
}

/// Tells the overlay that the picture changed: it asks for it again.
fn tell_overlay(app: &AppHandle) {
    if let Some(overlay) = app.try_state::<Arc<crate::overlay::Overlay>>() {
        overlay.avatar_changed();
    }
}

/// Opens the file dialog of Windows for a picture (PNG, JPG, WebP or GIF) and reads the one
/// chosen; returns it as a data URL for the crop dialog, or `None` when the dialog was closed.
#[tauri::command]
pub async fn avatar_pick(app: AppHandle, webview: Webview) -> Result<Option<String>, String> {
    crate::commands::ensure_caller(webview.label(), crate::MAIN_WINDOW)?;
    let store = store(&app)?;
    let window = webview.window();
    tauri::async_runtime::spawn_blocking(move || {
        use tauri_plugin_dialog::DialogExt;
        let chosen = app
            .dialog()
            .file()
            .set_parent(&window)
            .add_filter(
                "PNG, JPG, WebP, GIF",
                &["png", "jpg", "jpeg", "webp", "gif"],
            )
            .blocking_pick_file();
        let Some(chosen) = chosen else {
            return Ok(None);
        };
        let path = chosen.into_path().map_err(|_| BAD_KIND.to_owned())?;
        store.pick(&path).map(Some)
    })
    .await
    .map_err(|err| format!("a background task failed: {err}"))?
}

/// Keeps the picture last picked, cut as `crop` says, as Raphael's picture, and tells the overlay.
#[tauri::command]
pub fn avatar_save(app: AppHandle, webview: Webview, crop: Crop) -> Result<(), String> {
    crate::commands::ensure_caller(webview.label(), crate::MAIN_WINDOW)?;
    store(&app)?.save(crop)?;
    tell_overlay(&app);
    Ok(())
}

/// The picture and its crop, or `None` while Raphael is his emblem. The main window and the
/// overlay may ask for it.
#[tauri::command]
pub fn avatar_get(app: AppHandle, webview: Webview) -> Result<Option<Picture>, String> {
    if crate::commands::ensure_caller(webview.label(), crate::MAIN_WINDOW).is_err() {
        crate::commands::ensure_caller(webview.label(), crate::OVERLAY_WINDOW)?;
    }
    Ok(store(&app)?.load())
}

/// Deletes the picture, and tells the overlay: Raphael is his emblem again.
#[tauri::command]
pub fn avatar_clear(app: AppHandle, webview: Webview) -> Result<(), String> {
    crate::commands::ensure_caller(webview.label(), crate::MAIN_WINDOW)?;
    store(&app)?.clear()?;
    tell_overlay(&app);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const PNG: &[u8] = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR";
    const WEBP: &[u8] = b"RIFF\x10\0\0\0WEBPVP8 ";

    fn store() -> (AvatarStore, PathBuf) {
        let dir = std::env::temp_dir().join(format!("raphael-avatar-{}", uuid::Uuid::new_v4()));
        (AvatarStore::new(&dir), dir)
    }

    fn file(dir: &Path, name: &str, bytes: &[u8]) -> PathBuf {
        fs::create_dir_all(dir).unwrap();
        let path = dir.join(name);
        fs::write(&path, bytes).unwrap();
        path
    }

    #[test]
    fn the_kind_comes_from_the_bytes_and_only_four_are_taken() {
        assert_eq!(kind_of(PNG), Some("image/png"));
        assert_eq!(kind_of(&[0xff, 0xd8, 0xff, 0xe0]), Some("image/jpeg"));
        assert_eq!(kind_of(b"GIF89a...."), Some("image/gif"));
        assert_eq!(kind_of(b"GIF87a...."), Some("image/gif"));
        assert_eq!(kind_of(WEBP), Some("image/webp"));
        for bad in [
            &b""[..],
            b"<svg xmlns",
            b"RIFF\0\0\0\0WAVEfmt ",
            b"BM\0\0",
            b"%PDF-1.7",
        ] {
            assert_eq!(kind_of(bad), None, "{bad:?}");
        }
    }

    #[test]
    fn base64_is_the_standard_alphabet_with_padding() {
        assert_eq!(base64(b""), "");
        assert_eq!(base64(b"f"), "Zg==");
        assert_eq!(base64(b"fo"), "Zm8=");
        assert_eq!(base64(b"foo"), "Zm9v");
        assert_eq!(base64(b"foobar"), "Zm9vYmFy");
        assert_eq!(base64(&[0xfb, 0xff]), "+/8=");
        assert_eq!(data_url("image/png", b"foo"), "data:image/png;base64,Zm9v");
    }

    #[test]
    fn a_crop_out_of_range_is_refused() {
        let fine = Crop {
            x: 0.5,
            y: 0.0,
            zoom: 1.0,
        };
        assert_eq!(fine.checked(), Ok(fine));
        for bad in [
            Crop { x: -0.1, ..fine },
            Crop { y: 1.1, ..fine },
            Crop { zoom: 0.9, ..fine },
            Crop { zoom: 4.5, ..fine },
            Crop {
                x: f64::NAN,
                ..fine
            },
            Crop {
                zoom: f64::INFINITY,
                ..fine
            },
        ] {
            assert!(bad.checked().is_err(), "{bad:?}");
        }
    }

    #[test]
    fn a_picture_picked_saved_read_and_cleared() {
        let (store, dir) = store();
        assert_eq!(store.load(), None, "the emblem until a picture is saved");
        let crop = Crop {
            x: 0.3,
            y: 0.6,
            zoom: 1.5,
        };
        assert_eq!(store.save(crop), Err(NOTHING_PICKED.to_owned()));
        let source = file(&dir.join("source"), "me.png", PNG);
        assert_eq!(store.pick(&source).unwrap(), data_url("image/png", PNG));
        store.save(crop).unwrap();
        // The copy is the picture's: the file it came from may go.
        fs::remove_file(&source).unwrap();
        assert_eq!(
            store.load(),
            Some(Picture {
                url: data_url("image/png", PNG),
                crop
            })
        );
        assert_eq!(
            store.save(crop),
            Err(NOTHING_PICKED.to_owned()),
            "a pick is saved once"
        );
        store.clear().unwrap();
        assert_eq!(store.load(), None);
        store.clear().unwrap(); // nothing to delete is fine
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn what_is_not_a_picture_or_too_large_is_refused_and_keeps_nothing() {
        let (store, dir) = store();
        let svg = file(&dir, "a.png", b"<svg xmlns='http://www.w3.org/2000/svg'/>");
        assert_eq!(store.pick(&svg), Err(BAD_KIND.to_owned()));
        let mut big = PNG.to_vec();
        big.resize(usize::try_from(MAX_BYTES).unwrap() + 1, 0);
        let large = file(&dir, "b.png", &big);
        assert_eq!(store.pick(&large), Err(TOO_LARGE.to_owned()));
        let crop = Crop {
            x: 0.5,
            y: 0.5,
            zoom: 1.0,
        };
        assert_eq!(store.save(crop), Err(NOTHING_PICKED.to_owned()));
        assert!(store.pick(&dir.join("missing.png")).is_err());
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn a_kept_picture_that_was_changed_into_something_else_is_not_shown() {
        let (store, dir) = store();
        store.pick(&file(&dir, "a.webp", WEBP)).unwrap();
        store
            .save(Crop {
                x: 0.5,
                y: 0.5,
                zoom: 2.0,
            })
            .unwrap();
        assert!(store.load().is_some());
        fs::write(dir.join(DIR).join(PICTURE), b"<html>").unwrap();
        assert_eq!(store.load(), None);
        fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn every_command_checks_who_calls_it() {
        let source = include_str!("avatar.rs").replace("\r\n", "\n");
        let production = source.split("#[cfg(test)]").next().unwrap();
        let commands: Vec<&str> = production.split("#[tauri::command]").skip(1).collect();
        assert_eq!(commands.len(), 4);
        for command in commands {
            let body = command.split("\n}\n").next().unwrap();
            assert!(
                body.contains("ensure_caller(webview.label(), crate::MAIN_WINDOW)"),
                "{body}"
            );
        }
    }
}
