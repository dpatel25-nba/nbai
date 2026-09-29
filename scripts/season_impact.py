"""Select pre-season impact evidence without confusing absence with poor play."""
import math


def impact_profiles(rows, player_ids, target_season, lookback=3, prior=-1.5):
    """Keep current ratings; carry the latest finite rating within the rate window.

    Missing a season is not an observed replacement-level performance. Carrying
    history is an explicit assumption, not a fitted injury-recovery forecast.
    Evidence outside the same three-season window used for player rates remains
    a labeled stale-history prior. Never read target-season or future outcomes.
    """
    target = int(target_season[:4])
    history, seen = {}, set()
    for row in rows:
        year = int(row['SEASON'][:4])
        if year >= target:
            continue
        pid = int(row['PLAYER_ID'])
        key = (pid, year)
        if key in seen:
            raise ValueError('Duplicate player-season impact evidence: ' + str(key))
        seen.add(key)
        value = row['BPM3']
        if value is None or not math.isfinite(float(value)):
            continue
        if pid not in history or year > history[pid][0]:
            history[pid] = (year, row['SEASON'], float(value))
    profiles = {}
    for pid in map(int, player_ids):
        latest = history.get(pid)
        gap = target - latest[0] if latest else None
        usable = latest is not None and gap <= lookback
        profiles[pid] = dict(
            bpm=round(latest[2] if usable else prior, 4),
            impactSource=('previous_season' if gap == 1 else 'historical_carry_forward')
                if usable else ('stale_history_prior' if latest else 'no_history_prior'),
            impactSeason=latest[1] if usable else None,
            impactLastAvailableSeason=latest[1] if latest else None,
            impactSeasonGap=gap,
        )
    return profiles
