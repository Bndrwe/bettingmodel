"""Collect actual tennis match outcomes for a past day.

tennisexplorer.com's schedule page is stateful by date -- refetching the same
URL for a day that has already happened returns the same rows but with the
score cells populated.

IMPORTANT: for completed matches the site re-renders the pair with the
WINNER in the first row. Row position therefore encodes the outcome on the
results page but NOT on the morning schedule page the predictions were
built from, so results must record the winner's *identity* (slug/name) --
grading by "player1"/"player2" position silently compared two different
orderings and scored ~94% of every day's matches as player1 wins.
"""
import json
import re
import sys
import unicodedata
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tennis_model import BASE, fetch, parse_schedule  # noqa: E402


def _games_won(cell):
    """Games won in a set from a tennisexplorer score cell.

    A tiebreak set is written as the winner's games followed by the loser's
    tiebreak points with no separator: "64" means 6 games (opponent lost the
    breaker 6-4), "710" means 7 games. Parsing those with int() yielded 64
    and 710, so every tiebreak set was scored as a blowout for whichever
    side happened to carry the tiebreak digits."""
    txt = (cell or "").strip()
    if not txt or not txt.isdigit():
        return None
    if len(txt) == 1:
        return int(txt)
    # Multi-digit: the leading digit is the game count (always 6 or 7 for a
    # completed tiebreak set); the remainder is the loser's breaker points.
    lead = int(txt[0])
    return lead if lead in (6, 7) else int(txt)


def _sets_won(p1_scores, p2_scores):
    sets1 = sets2 = 0
    for a, b in zip(p1_scores, p2_scores):
        av, bv = _games_won(a), _games_won(b)
        if av is None or bv is None:
            continue
        if av > bv:
            sets1 += 1
        elif bv > av:
            sets2 += 1
    return sets1, sets2


def norm_name(name):
    """Normalize a player display name for cross-render comparison
    ("Müller T." / "Muller T." -> "mullert")."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z]", "", s.lower())


def determine_winner(m):
    """Return ("player1"|"player2", sets1, sets2) relative to THIS scrape's
    row order, or (None, ...) when it can't be resolved."""
    sets1, sets2 = _sets_won(m.get("player1Scores", []), m.get("player2Scores", []))
    if sets1 == sets2:
        return None, sets1, sets2
    return ("player1" if sets1 > sets2 else "player2"), sets1, sets2


def determine_first_set_winner(m):
    """Whoever won more games in the first score column won set 1. Ties
    (game counts equal, e.g. a retirement before the set finished) can't
    be resolved from game counts alone, so those are left ungraded rather
    than guessed."""
    p1s, p2s = m.get("player1Scores", []), m.get("player2Scores", [])
    if not p1s or not p2s:
        return None
    a, b = _games_won(p1s[0]), _games_won(p2s[0])
    if a is None or b is None or a == b:
        return None
    return "player1" if a > b else "player2"


def main():
    yesterday = date.today() - timedelta(days=1)
    try:
        html = fetch(
            f"{BASE}/matches/?type=all&year={yesterday.year}"
            f"&month={yesterday.month:02d}&day={yesterday.day:02d}"
        )
        matches = parse_schedule(html)
    except Exception as e:
        print(f"Result collection failed: {type(e).__name__}: {e}")
        matches = []

    results = []
    for m in matches:
        if not m.get("matchId"):
            continue
        winner_pos, sets1, sets2 = determine_winner(m)
        if winner_pos is None:
            continue
        fs_pos = determine_first_set_winner(m)

        def ident(pos):
            n = "1" if pos == "player1" else "2"
            return m.get(f"player{n}Slug"), m.get(f"player{n}")

        win_slug, win_name = ident(winner_pos)
        fs_slug, fs_name = ident(fs_pos) if fs_pos else (None, None)

        results.append({
            "matchId": m["matchId"],
            # Identity of the winner, NOT a row position -- the results page
            # reorders completed matches so the winner is listed first.
            "winnerSlug": win_slug,
            "winnerName": win_name,
            "winnerNameNorm": norm_name(win_name),
            "firstSetWinnerSlug": fs_slug,
            "firstSetWinnerName": fs_name,
            "firstSetWinnerNameNorm": norm_name(fs_name),
            "sets": [sets1, sets2],
            "player1Scores": m.get("player1Scores", []),
            "player2Scores": m.get("player2Scores", []),
        })

    out_dir = Path("data/tennis_results")
    out_dir.mkdir(exist_ok=True, parents=True)
    with open(out_dir / f"results_{yesterday.isoformat()}.json", "w") as f:
        json.dump({"date": yesterday.isoformat(), "results": results}, f, indent=2)

    print(f"Collected {len(results)} completed tennis results for {yesterday.isoformat()}")


if __name__ == "__main__":
    main()
