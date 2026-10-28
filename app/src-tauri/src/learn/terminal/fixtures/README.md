# Terminal layout fixture

`herdr-anonymized.txt` preserves the 49 rows, spaces, line breaks, punctuation, prompt markers and box-drawing geometry of the supplied Windows Terminal UIA sample. It is test data, never a runtime capture.

All Latin words are replaced with a fixed synthetic vocabulary chosen only by word length, preserving the original uppercase/lowercase positions. Longer identifiers use a repeated synthetic word. All digits become zero, and Han characters are replaced in sequence with characters from a synthetic Traditional Chinese phrase. These substitutions are not reversible encodings of the original words. Each replacement retains its script and terminal cell width. No original prose, names, paths or identifiers are used by the tests. The original sample is not copied into the repository.

The three-row synthetic paste near the top of the middle column is used to check that a correction in that column is learned while updates in the left and right columns are ignored.
