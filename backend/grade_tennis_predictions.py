"""Grade yesterday's tennis picks against actual results and close the
self-learning loop -- same pattern as grade_game_predictions.py for MLB.
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collect_tennis_results import norm_name  # noqa: E402

CONFIDENCE_BUCKETS = [
    (0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70),
    (0.70, 0.75), (0.75, 0.80), (0.80, 0.90), (0.90, 1.01),
]


def _load(path):
    p = Path(path)
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


def _winner_position(pred, result, key):
    """Map a result's recorded winner IDENTITY onto this prediction's
    player1/player2 slots.

    The results page re-renders completed matches with the winner first, so
    the old positional comparison (pred["pick"] == result["winner"]) matched
    the morning ordering against the post-match ordering -- ~94% of every
    day's matches graded as "player1 won", manufacturing a 97.5% hit rate
    for picks that were literally "whoever was listed first"."""
    slug = result.get(f"{key}Slug")
    name_norm = result.get(f"{key}NameNorm")
    if not slug and not name_norm:
        # Legacy results file written before identities were recorded --
        # ungradeable, because its "player1" refers to a different ordering.
        return None
    for pos in ("player1", "player2"):
        p = pred.get(pos) or {}
        if slug and p.get("slug") and p["slug"] == slug:
            return pos
        if name_norm and norm_name(p.get("name")) == name_norm:
            return pos
    return None


def grade_day(date_str):
    preds = _load(f"data/history/tennis_{date_str}.json")
    results = _load(f"data/tennis_results/results_{date_str}.json")
    if not preds or not results:
        print(f"Missing tennis predictions or results for {date_str} -- skipping")
        return None

    results_by_id = {r["matchId"]: r for r in results.get("results", [])}
    graded = []
    for m in preds.get("matches", []):
        r = results_by_id.get(m.get("matchId"))
        if not r:
            continue

        winner_pos = _winner_position(m, r, "winner")
        if winner_pos is None:
            # Can't tie the recorded winner back to either predicted player
            # (renamed/re-slugged entry) -- skip rather than guess.
            continue
        prob = max(m["modelProb"]["player1"], m["modelProb"]["player2"])
        # A match with no real signal (no odds, no ranking for either
        # player) carries pick=None and must not be scored -- counting
        # "whoever the site listed first" as a pick is what produced the
        # bogus 97.5% ranking-only hit rate.
        hit = None if not m.get("pick") else (m["pick"] == winner_pos)

        fs = m.get("firstSet") or {}
        fs_pos = _winner_position(m, r, "firstSetWinner")
        fs_hit, fs_prob = None, None
        if fs and fs.get("pick") and fs_pos is not None:
            fs_prob = max(fs.get("player1", 0.5), fs.get("player2", 0.5))
            fs_hit = fs.get("pick") == fs_pos

        graded.append({
            "matchId": m.get("matchId"),
            "matchup": f"{m['player1']['name']} vs {m['player2']['name']}",
            "tour": m.get("tour"),
            "tournament": m.get("tournament"),
            "pick": m.get("pick"),
            "winner": winner_pos,
            "winnerName": r.get("winnerName"),
            "prob": round(prob, 4),
            "source": m.get("source"),
            "hit": hit,
            "firstSet": {
                "pick": fs.get("pick"),
                "winner": fs_pos,
                "prob": round(fs_prob, 4) if fs_prob is not None else None,
                "hit": fs_hit,
            },
        })
    return graded


def update_accuracy_log(date_str, graded):
    log_path = Path("data/tennis_accuracy_log.json")
    log = _load(log_path) or {"sessions": []}

    hits = sum(1 for g in graded if g["hit"] is True)
    fs_hits = sum(1 for g in graded if g["firstSet"].get("hit") is True)
    fs_total = sum(1 for g in graded if g["firstSet"].get("hit") is not None)
    session = {
        "date": date_str,
        "matchesGraded": len(graded),
        "hits": hits,
        "total": sum(1 for g in graded if g["hit"] is not None),
        "firstSet": {"hits": fs_hits, "total": fs_total},
        "detail": graded,
    }
    log["sessions"] = [s for s in log.get("sessions", []) if s.get("date") != date_str]
    log["sessions"].append(session)
    log["sessions"].sort(key=lambda s: s["date"])
    log["sessions"] = log["sessions"][-60:]

    with open(log_path, "w") as f:
        json.dump(log, f, indent=2)

    graded_total = sum(1 for g in graded if g["hit"] is not None)
    print(f"Graded {len(graded)} tennis matches for {date_str}: {hits}/{graded_total} hit "
          f"(first set: {fs_hits}/{fs_total})")
    return log


def _bucket_stats(entries):
    """entries: iterable of (prob, hit) pairs -> filled confidence buckets."""
    buckets = [{"lo": lo, "hi": hi, "total": 0, "hits": 0, "sumExpected": 0.0}
               for lo, hi in CONFIDENCE_BUCKETS]
    for prob, hit in entries:
        if prob is None or hit is None:
            continue
        for b in buckets:
            if b["lo"] <= prob < b["hi"]:
                b["total"] += 1
                if hit:
                    b["hits"] += 1
                b["sumExpected"] += prob
                break
    return buckets


def update_calibration(log):
    sessions = log.get("sessions", [])
    buckets = _bucket_stats(
        (g.get("prob"), g.get("hit")) for s in sessions for g in s.get("detail", [])
    )
    fs_buckets = _bucket_stats(
        (g.get("firstSet", {}).get("prob"), g.get("firstSet", {}).get("hit"))
        for s in sessions for g in s.get("detail", [])
    )

    weights_path = Path("data/tennis_model_weights.json")
    weights = _load(weights_path) or {}
    weights["calibrationBuckets"] = buckets
    weights["firstSetCalibrationBuckets"] = fs_buckets
    weights["accuracySummary"] = {
        "hits": sum(b["hits"] for b in buckets),
        "total": sum(b["total"] for b in buckets),
    }
    weights["firstSetAccuracySummary"] = {
        "hits": sum(b["hits"] for b in fs_buckets),
        "total": sum(b["total"] for b in fs_buckets),
    }
    weights["lastUpdated"] = date.today().isoformat()
    with open(weights_path, "w") as f:
        json.dump(weights, f, indent=2)
    print(f"Updated tennis calibration across {sum(b['total'] for b in buckets)} match picks, "
          f"{sum(b['total'] for b in fs_buckets)} first-set picks")


def main():
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    graded = grade_day(yesterday)
    if not graded:
        return
    log = update_accuracy_log(yesterday, graded)
    update_calibration(log)


if __name__ == "__main__":
    main()
