"""Read-only recovery plan for legacy dictation statistics. Never retains or logs field text.

Cleanup has no language tag. Fit words/token from retained successful cleanups paired by model
and completion time; use a model-specific ratio with at least three samples, otherwise the pooled
ratio. Without overlap use one word/token, explicitly estimated, rather than claiming precision.
Missing cleanup calls approximate dictations (old deleted chunked/cancelled calls are ambiguous).
Retained chunks are matched together and never treated as missing dictations.
"""

import sqlite3
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import tzinfo

from .stats import count_words, local_day


@dataclass
class DailyAggregate:
    day: str
    dictations: int = 0
    words: int = 0
    audio_seconds: float = 0.0
    processing_ms: int = 0
    estimated: int = 0
    estimated_words: int = 0
    estimated_dictations: int = 0


@dataclass
class RecoveryPlan:
    days: dict[str, DailyAggregate] = field(default_factory=dict)
    words_per_token: float = 1.0
    model_ratios: dict[str, float] = field(default_factory=dict)
    paired_dictations: int = 0
    paired_words: int = 0
    paired_tokens: int = 0
    audio_seconds_per_word: float = 0.0
    processing_ms_per_dictation: float = 0.0


def estimate_recovery(conn: sqlite3.Connection, tz: tzinfo | None = None) -> RecoveryPlan:
    """Build exact daily totals plus marked estimates using SELECTs only, even on a mode=ro DB.

    A cleanup is paid just before a history row is finalized. Match unclaimed calls of that row's
    model within its measured cleanup duration plus a two-second bookkeeping allowance. Only
    successful nonempty matches calibrate words/token. Unmatched retained rows still offset the
    cleanup count, conservatively covering local-only/fallback dictations. Recover the oldest
    unmatched calls first on partially pruned days. Costs are never estimated or rewritten.
    """
    plan = RecoveryPlan()
    calls = list(conn.execute(
        "SELECT id, created_at_ms, completion_tokens, model FROM spend"
        " WHERE purpose = 'cleanup' ORDER BY created_at_ms, id"
    ))
    times = [row[1] for row in calls]
    used: set[int] = set()
    unpaired_history: dict[str, int] = defaultdict(int)
    # model -> [sample count, words, tokens]
    models: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    audio = processing = words_total = count = 0
    for ms, text, seconds, stt, llm, model, cleaned in conn.execute(
        "SELECT created_at_ms, text, audio_seconds, stt_ms, llm_ms, model, cleaned FROM history"
        " WHERE kind = 'dictation' ORDER BY created_at_ms, id"
    ):
        day = local_day(ms, tz).isoformat()
        total = plan.days.setdefault(day, DailyAggregate(day))
        words = count_words(text)
        total.dictations += 1
        total.words += words
        total.audio_seconds += seconds
        total.processing_ms += stt + llm
        audio += seconds
        processing += stt + llm
        words_total += words
        count += 1
        candidates = [row for row in calls[
            bisect_left(times, ms - max(llm + 2000, 2000)):bisect_right(times, ms)
        ] if row[0] not in used and model is not None and row[3] == model]
        used.update(row[0] for row in candidates)
        if not candidates:
            unpaired_history[day] += 1
        tokens = sum(max(0, row[2]) for row in candidates)
        if cleaned and words > 0 and tokens > 0:
            plan.paired_dictations += 1
            plan.paired_words += words
            plan.paired_tokens += tokens
            fit = models[model]
            fit[0] += 1
            fit[1] += words
            fit[2] += tokens
    if plan.paired_tokens:
        plan.words_per_token = plan.paired_words / plan.paired_tokens
    plan.model_ratios = {
        model: words / tokens for model, (samples, words, tokens) in models.items() if samples >= 3
    }
    if words_total:
        plan.audio_seconds_per_word = audio / words_total
    if count:
        plan.processing_ms_per_dictation = processing / count

    missing_by_day: dict[str, list[tuple]] = defaultdict(list)
    for row in calls:
        if row[0] not in used:
            missing_by_day[local_day(row[1], tz).isoformat()].append(row)
    for day, unmatched in missing_by_day.items():
        missing = max(0, len(unmatched) - unpaired_history[day])
        if not missing:
            continue
        recovered_words = round(sum(
            max(0, row[2]) * plan.model_ratios.get(row[3], plan.words_per_token)
            for row in unmatched[:missing]
        ))
        total = plan.days.setdefault(day, DailyAggregate(day))
        total.dictations += missing
        total.words += recovered_words
        total.audio_seconds += recovered_words * plan.audio_seconds_per_word
        total.processing_ms += round(missing * plan.processing_ms_per_dictation)
        total.estimated = 1
        total.estimated_words = recovered_words
        total.estimated_dictations = missing
    return plan
