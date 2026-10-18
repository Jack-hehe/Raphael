//! Optional Windows word boundaries. No text or dictionary is retained by this provider.

use std::ops::Range;

/// Scalar-character ranges, matching Tracker's indexes. Unsupported languages fail closed.
/// Production calls this only when the learning watch finishes on its own worker thread.
pub(super) fn word_ranges(text: &str) -> Option<Vec<Range<usize>>> {
    #[cfg(windows)]
    {
        windows_words(text)
    }
    #[cfg(not(windows))]
    {
        let _ = text;
        None
    }
}

#[cfg(windows)]
fn windows_words(text: &str) -> Option<Vec<Range<usize>>> {
    use windows::Data::Text::{IWordsSegmenterFactory, WordsSegmenter};
    use windows::Win32::System::WinRT::{RO_INIT_MULTITHREADED, RoInitialize, RoUninitialize};
    use windows::core::{HSTRING, Interface};

    struct Apartment;
    impl Drop for Apartment {
        fn drop(&mut self) {
            // SAFETY: balanced with the successful initialization on this same watch thread.
            unsafe { RoUninitialize() };
        }
    }

    // SAFETY: all returned COM objects are dropped before this same thread's apartment guard.
    unsafe { RoInitialize(RO_INIT_MULTITHREADED) }.ok()?;
    let _apartment = Apartment;
    // A fresh factory must drop before the apartment. The generated cached constructor can
    // retain a factory across RoUninitialize, which is unsafe when the next watch creates one.
    let factory = windows::core::factory::<WordsSegmenter, IWordsSegmenterFactory>().ok()?;
    let language = HSTRING::from("zh-TW");
    let mut raw = std::ptr::null_mut();
    // SAFETY: this is the generated constructor's ABI, using a fresh factory instead of its
    // global cache. The HSTRING and factory stay alive for the call; success owns the interface.
    let segmenter = unsafe {
        (Interface::vtable(&factory).CreateWithLanguage)(
            Interface::as_raw(&factory),
            std::mem::transmute_copy(&language),
            &mut raw,
        )
        .ok()
        .ok()?;
        if raw.is_null() {
            return None;
        }
        WordsSegmenter::from_raw(raw)
    };
    if !segmenter
        .ResolvedLanguage()
        .ok()?
        .to_string_lossy()
        .eq_ignore_ascii_case("zh-TW")
    {
        return None;
    }
    let chars: Vec<char> = text.chars().collect();
    let positions = scalar_positions(&chars);
    let tokens = segmenter.GetTokens(&HSTRING::from(text)).ok()?;
    let mut ranges = Vec::new();
    let mut previous_end = 0;
    for index in 0..tokens.Size().ok()? {
        let token = tokens.GetAt(index).ok()?;
        let source = token.SourceTextSegment().ok()?;
        let range = scalar_range(&positions, source.StartPosition, source.Length)?;
        if range.is_empty() || range.start < previous_end || range.end > chars.len() {
            return None;
        }
        let original: String = chars[range.clone()].iter().collect();
        if token.Text().ok()?.to_string_lossy() != original {
            return None;
        }
        previous_end = range.end;
        ranges.push(range);
    }
    Some(ranges)
}

/// Only real UTF-16 character boundaries map to scalar indexes; surrogate middles stay None.
#[cfg(any(windows, test))]
fn scalar_positions(chars: &[char]) -> Vec<Option<usize>> {
    let mut positions = vec![Some(0)];
    for (index, character) in chars.iter().enumerate() {
        if character.len_utf16() == 2 {
            positions.push(None);
        }
        positions.push(Some(index + 1));
    }
    positions
}

#[cfg(any(windows, test))]
fn scalar_range(positions: &[Option<usize>], start: u32, length: u32) -> Option<Range<usize>> {
    let end = start.checked_add(length)?;
    let start = positions.get(start as usize).copied().flatten()?;
    let end = positions.get(end as usize).copied().flatten()?;
    Some(start..end)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn utf16_offsets_map_complete_scalars_after_surrogates() {
        let positions = scalar_positions(&"😀手機𠮷".chars().collect::<Vec<_>>());
        assert_eq!(scalar_range(&positions, 2, 2), Some(1..3));
        assert_eq!(scalar_range(&positions, 4, 2), Some(3..4));
        assert_eq!(scalar_range(&positions, 0, 2), Some(0..1));
        assert_eq!(scalar_range(&positions, 1, 1), None);
        assert_eq!(scalar_range(&positions, 4, 1), None);
        assert_eq!(scalar_range(&positions, 100, 2), None);
        assert_eq!(scalar_range(&positions, u32::MAX, 1), None);
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "needs the Windows zh-TW word breaker"]
    fn windows_provider_offsets_and_apartments_survive_repeated_watches() {
        for _ in 0..3 {
            std::thread::spawn(|| {
                for _ in 0..3 {
                    let words = word_ranges("😀手機。\n拉斐爾在臺北")
                        .expect("zh-TW word breaker is available");
                    assert!(words.contains(&(1..3)), "phone after a surrogate pair");
                    assert!(words.contains(&(5..8)), "name after a newline");
                    assert!(words.contains(&(9..11)), "traditional place name");
                }
            })
            .join()
            .unwrap();
        }
    }
}
