import numpy as np
import pandas as pd
import json
from pathlib import Path
from datetime import datetime

LG = {
    "avg": 0.244, "obp": 0.314, "slg": 0.408, "ops": 0.722,
    "iso": 0.155, "kPct": 0.228, "bbPct": 0.085, "hrPA": 0.0307,
    "fbPct": 0.38, "pullPct": 0.38, "linePct": 0.21,
    "pitHR9": 1.28, "pitKPct": 0.228, "pitBBPct": 0.085, "pitFBPct": 0.38,
    "xbhPA": 0.081, "hardHitPct": 0.38
}

def clamp(val, lo, hi):
    return max(lo, min(hi, val))

def regression_weight(pa):
    """Dynamic regression weight based on sample size"""
    if pa < 50:
        return 0.85  # Heavy regression for tiny samples
    elif pa < 100:
        return 0.70
    elif pa < 200:
        return 0.50
    elif pa < 400:
        return 0.30
    else:
        return 0.15  # Light regression for large samples

def est_barrel(iso):
    return clamp((iso / LG["iso"]) * 0.078, 0.04, 0.16)

def est_pull_rate(hr, ab):
    hr_rate = hr / max(ab, 1)
    return clamp(0.35 + 0.10 * (hr_rate / LG["hrPA"]), 0.25, 0.50)

def est_hard_contact(slg, avg, xbh, pa):
    """Estimate hard contact rate from SLG-AVG and XBH rate"""
    if pa < 20:
        return LG["hardHitPct"]
    slg_iso = slg - avg
    xbh_rate = xbh / pa if pa > 0 else LG["xbhPA"]
    hard_proxy = 0.60 * (slg_iso / LG["iso"]) + 0.40 * (xbh_rate / LG["xbhPA"])
    return clamp(hard_proxy * LG["hardHitPct"], 0.25, 0.55)

def est_sprint_speed_boost(sb, pa):
    """Sprint speed proxy from stolen bases: high SB rate suggests speed over power"""
    if pa < 50:
        return 1.0
    sb_rate = sb / pa if pa > 0 else 0
    if sb_rate > 0.15:
        return 0.96  # Slight penalty
    return 1.0

def wind_effect(wind_speed, wind_dir, cf_bearing):
    if wind_speed < 2:
        return 1.0
    wind_to = (wind_dir + 180) % 360
    diff = abs(wind_to - cf_bearing)
    if diff > 180:
        diff = 360 - diff
    component = np.cos(np.radians(diff))
    return clamp(1 + component * wind_speed * 0.0048, 0.78, 1.22)

def lineup_pa(order):
    """Expected plate appearances by lineup slot (league-typical values,
    matching the client-side model's table in index.html)."""
    return [4.65, 4.5, 4.35, 4.2, 4.1, 3.95, 3.85, 3.75, 3.65][order - 1] if 1 <= order <= 9 else 4.1

def count_advantage(k_pct, bb_pct, pit_k_pct, pit_bb_pct):
    batter_disc = bb_pct - k_pct
    pitcher_disc = pit_bb_pct - pit_k_pct
    return clamp(1 + 0.08 * (batter_disc - pitcher_disc), 0.92, 1.12)

def k_rate_trend(k_recent, k_season, pa_recent):
    """Recent K-rate improvement: lower K% recently = better contact = HR boost"""
    if pa_recent < 20:
        return 1.0
    k_diff = k_season - k_recent  # Positive if recent K% is lower (better)
    return clamp(1 + k_diff * 0.35, 0.90, 1.12)

def bullpen_vulnerability(team_bullpen_hr9):
    """Opposing pitching staff HR/9 rate (team-level proxy for bullpen quality).
    Centered so a league-average staff yields exactly 1.0 (the old
    0.92 + 0.16*(ratio-1) form penalized every batter 8% at neutral)."""
    if team_bullpen_hr9 is None:
        return 1.0
    mult = team_bullpen_hr9 / LG["pitHR9"]
    return clamp(1 + 0.16 * (mult - 1), 0.85, 1.18)

def pitcher_fatigue_factor(pitcher_ip_season):
    """Pitcher fatigue from cumulative innings pitched"""
    if pitcher_ip_season is None or pitcher_ip_season < 30:
        return 1.0
    if pitcher_ip_season > 160:
        fatigue = (pitcher_ip_season - 160) / 40  # Every 40 IP past 160 = +2.5% HR rate
        return clamp(1 + fatigue * 0.025, 1.0, 1.10)
    return 1.0

def park_hand_factor(park_lhf, park_rhf, batter_hand):
    """Use hand-specific park factors"""
    if batter_hand == "L":
        return park_lhf if park_lhf else 1.0
    elif batter_hand == "R":
        return park_rhf if park_rhf else 1.0
    else:  # Switch hitter
        return (park_lhf + park_rhf) / 2 if (park_lhf and park_rhf) else 1.0

def pitcher_hr_k_mult(pitcher_stat):
    """Regress the starter's actual HR/9 and K% toward league average by sample
    size (battersFaced), then convert to multipliers. Previously these were
    hard-coded to 1.0 even though real pitcher stats were being fetched."""
    if not pitcher_stat:
        return 1.0, 1.0
    bf = pitcher_stat.get("bf", 0) or 0
    prw = clamp(bf / 400, 0.05, 0.90)
    reg_hr9 = prw * pitcher_stat.get("hr9", LG["pitHR9"]) + (1 - prw) * LG["pitHR9"]
    hr_mult = clamp(reg_hr9 / LG["pitHR9"], 0.55, 1.85)
    reg_kpct = prw * pitcher_stat.get("kPct", LG["pitKPct"]) + (1 - prw) * LG["pitKPct"]
    k_mult = clamp(1 - 0.12 * max(0, reg_kpct - LG["pitKPct"]), 0.80, 1.06)
    return hr_mult, k_mult

def fb_factor(pitcher_stat):
    """Pitcher fly-ball tendency (estimated from groundout/airout ratio).
    Higher FB% pitchers surrender more home runs."""
    fb_pct = pitcher_stat.get("fbPct", LG["pitFBPct"]) if pitcher_stat else LG["pitFBPct"]
    return clamp(0.90 + 0.10 * (fb_pct / LG["pitFBPct"]), 0.90, 1.10)

def whip_factor(pitcher_stat):
    whip = pitcher_stat.get("whip", 1.30) if pitcher_stat else 1.30
    return clamp(0.93 + 0.07 * (whip / 1.30), 0.93, 1.12)


def _load_calibration():
    """Blend recent actual vs. expected hit-rate from data/training_log.json
    into a small bounded global self-calibration multiplier. This closes the
    loop that train_model.py's daily comparisons previously fed nowhere --
    the AI suggestions were logged but never actually adjusted a live
    prediction."""
    path = Path(__file__).resolve().parent.parent / "data" / "training_log.json"
    try:
        with open(path) as f:
            log = json.load(f)
        sessions = log.get("training_sessions", [])[-14:]
        if len(sessions) < 5:
            return 1.0
        total = sum(s.get("total_predictions", 0) for s in sessions)
        hits = sum(s.get("hits", 0) for s in sessions)
        if total < 100:
            return 1.0
        observed = hits / total
        # Predictions are the top-N by gameProb each day, which historically
        # clusters around an ~11-14% average HR probability.
        expected = 0.125
        drift = observed - expected
        return clamp(1.0 + drift * 0.5, 0.94, 1.06)
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ZeroDivisionError, TypeError):
        return 1.0

GLOBAL_CALIBRATION_MULT = _load_calibration()


def _load_model_weights():
    path = Path(__file__).resolve().parent.parent / "data" / "model_weights.json"
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

_TIER_BUCKETS = _load_model_weights().get("tierCalibration", [])


def tier_calibration_mult(prelim_prob):
    """Bucket-level learning: precision_k.py walks every tracked slate and
    buckets predicted-vs-actual outcomes by probability tier into
    data/model_weights.json ("tierCalibration"). Buckets with fewer than 30
    tracked picks are ignored (too little evidence to trust), and the
    correction is dampened 40% and capped at +/-10% so no single bad
    stretch can swing the model hard."""
    for b in _TIER_BUCKETS:
        lo, hi, total = b.get("lo", 0), b.get("hi", 1), b.get("total", 0)
        if lo <= prelim_prob < hi and total >= 30:
            observed = b.get("hits", 0) / total
            expected = b.get("sumExpected", total * (lo + hi) / 2) / total
            drift = observed - expected
            return clamp(1.0 + drift * 0.40, 0.90, 1.10)
    return 1.0


def recent_form_mult(batter_l14, base_rate):
    """Factor 35: L14 HR-rate momentum. Uses the real last-14-game log
    fetched per batter (previously always passed in as None, so this
    signal never existed even though the fetch scaffolding was there)."""
    if not batter_l14 or batter_l14.get("pa", 0) < 20:
        return 1.0
    rr = batter_l14.get("hr", 0) / batter_l14.get("pa", 1)
    ratio = rr / max(base_rate, 0.001)
    return clamp(0.82 + 0.18 * ratio, 0.82, 1.35)


def streak7_mult(batter_l7, base_rate):
    """Factor 36: short-term (L7) hot/cold streak."""
    if not batter_l7 or batter_l7.get("pa", 0) < 12:
        return 1.0
    r7 = batter_l7.get("hr", 0) / batter_l7.get("pa", 1)
    ratio7 = r7 / max(base_rate, 0.001)
    return clamp(0.88 + 0.12 * ratio7, 0.88, 1.28)


def compute_model(batter, batter_stat, batter_l7, batter_l14, vs_hand, h2h,
                 pitcher_stat, pitcher_l3, game, weather, park_factor, season_day,
                 bullpen_hr9=None, pitcher_ip_season=None):

    pid = batter.get("id")
    name = batter.get("fullName") or f"{batter.get('first', '')} {batter.get('last', '')}".strip()
    team = game.get("team_abbr", "")
    opp_team = game.get("opp_abbr", "")
    game_matchup = f"{team} @ {opp_team}" if game.get("isAway") else f"{team} vs {opp_team}"

    bats = batter.get("bats") or batter.get("batSide", {}).get("code", "R")
    pit_throws = game.get("pitThrows", "R")
    platoon = (bats == "L" and pit_throws == "R") or (bats == "R" and pit_throws == "L")

    # Require minimum PA
    pa = batter_stat.get("pa", 0) if batter_stat else 0
    if pa < 40:
        return None

    ab = batter_stat.get("ab", 0) if batter_stat else 0
    hr = batter_stat.get("hr", 0) if batter_stat else 0
    bb = batter_stat.get("bb", 0) if batter_stat else 0
    k = batter_stat.get("k", 0) if batter_stat else 0
    sb = batter_stat.get("sb", 0) if batter_stat else 0
    dbl = batter_stat.get("dbl", 0) if batter_stat else 0
    trp = batter_stat.get("trp", 0) if batter_stat else 0

    avg = batter_stat.get("avg", LG["avg"]) if batter_stat else LG["avg"]
    slg = batter_stat.get("slg", LG["slg"]) if batter_stat else LG["slg"]
    # ISO was never populated by the caller, so this always fell back to the
    # league-average constant -- meaning iso_mult below was 1.0 for every
    # single batter regardless of their actual power. Compute it directly
    # from SLG - AVG (the standard ISO definition) whenever it isn't supplied.
    iso = batter_stat.get("iso") if (batter_stat and batter_stat.get("iso") is not None) else clamp(slg - avg, 0.02, 0.45)

    k_pct = k / pa if pa > 0 else LG["kPct"]
    bb_pct = bb / pa if pa > 0 else LG["bbPct"]
    xbh = dbl + trp + hr

    # Regression-weighted base rate
    rw = regression_weight(pa)
    raw_rate = hr / pa if pa > 0 else LG["hrPA"]
    base_rate = rw * LG["hrPA"] + (1 - rw) * raw_rate

    # Factor 35-36: L14 momentum / L7 streak -- batter_l7 and batter_l14 were
    # always passed in as None previously, so these signals never existed.
    recent_form = recent_form_mult(batter_l14, base_rate)
    streak7 = streak7_mult(batter_l7, base_rate)

    # === EXISTING FACTORS (1-29) ===

    # Factor 1: ISO multiplier -- dampened to 30% strength (matching the
    # client-side model) because barrel/zone/hard-contact/pull below are all
    # derived from the same ISO signal; at full strength the correlated
    # stack pushed every slugger straight into the probability cap.
    iso_mult = clamp(0.70 + 0.30 * (iso / LG["iso"]), 0.75, 1.45)

    # Factor 2: Platoon advantage
    platoon_mult = 1.15 if platoon else 0.96

    # Factor 3: Park factor (now hand-specific)
    if isinstance(park_factor, dict):
        park_lhf = park_factor.get("lhf", 1.0)
        park_rhf = park_factor.get("rhf", 1.0)
        park_mult = park_hand_factor(park_lhf, park_rhf, bats)
    else:
        park_mult = park_factor if park_factor else 1.0

    # Factor 4-5: Pitcher HR/9 and K% -- now driven by the real pitcher_stat
    # fetched from the MLB Stats API, instead of being hard-coded to 1.0.
    pitcher_hr_mult, pitcher_k_mult = pitcher_hr_k_mult(pitcher_stat)

    # Factor 6: Weather wind
    cf_bearing = park_factor.get("cfBearing", 0) if isinstance(park_factor, dict) else 0
    wind_speed = weather.get("windSpeed", 0) if weather else 0
    wind_dir = weather.get("windDir", 0) if weather else 0
    wind_mult = wind_effect(wind_speed, wind_dir, cf_bearing)

    # Factor 7: Temperature -- ~0.3% carry per degree F, centered at 70F.
    # The old form (0.93 + 0.001*(temp-70)) gave every batter a flat 7%
    # penalty in neutral weather and barely responded to real heat/cold.
    temp = weather.get("temp", 70) if weather else 70
    temp_mult = clamp(1 + 0.003 * (temp - 70), 0.88, 1.12)

    # Factor 8: Precipitation risk
    precip = weather.get("precipProb", 0) if weather else 0
    precip_mult = clamp(1 - 0.001 * precip, 0.93, 1.0)

    # Factor 9: Season phase
    season_mult = 1.0
    if season_day < 30:
        season_mult = 0.93
    elif season_day > 140:
        season_mult = 1.05

    # Factor 10: Lineup slot -- handled as *real expected plate appearances*
    # in the per-PA -> per-game conversion below (lineup_pa), not as yet
    # another multiplier on the per-PA rate, which double-counted the slot.
    order = game.get("order", 5)

    # Factor 11: Day/night
    is_day = game.get("dayNight", "night") == "day"
    day_night_mult = 0.97 if is_day else 1.02

    # Factor 12: H2H history -- compared against the batter's own regressed
    # rate (not the league's, which double-counted overall power) and
    # shrunk by sample size so a 12-PA career matchup can't swing 35%.
    h2h_mult = 1.0
    if h2h and h2h.get("pa", 0) >= 10:
        h2h_pa = h2h.get("pa", 1)
        h2h_rate = h2h.get("hr", 0) / h2h_pa
        h2h_w = clamp(h2h_pa / 40, 0.0, 0.60)
        h2h_mult = clamp(1 + 0.50 * h2h_w * (h2h_rate / max(base_rate, 0.001) - 1), 0.80, 1.30)

    # Factor 13: Vs hand splits -- blended toward the batter's own regressed
    # base rate by split sample size (mirrors the client-side model). The
    # old raw vs_rate/LG ratio re-applied the batter's overall power on top
    # of base_rate, so sluggers got the same edge counted twice.
    vs_mult = 1.0
    if vs_hand and vs_hand.get("pa", 0) >= 50:
        vs_pa = vs_hand.get("pa", 1)
        vs_rate = vs_hand.get("hr", 0) / vs_pa
        vs_w = clamp(vs_pa / 200, 0.0, 0.75)
        blended_vs = vs_w * vs_rate + (1 - vs_w) * base_rate
        vs_mult = clamp(blended_vs / max(base_rate, 0.001), 0.70, 1.40)

    # Factor 14-15: Count advantage -- now uses the real pitcher K%/BB% when
    # available instead of always plugging in the league average (which made
    # this factor a constant for every matchup).
    pit_k_pct = pitcher_stat.get("kPct", LG["pitKPct"]) if pitcher_stat else LG["pitKPct"]
    pit_bb_pct = pitcher_stat.get("bbPct", LG["pitBBPct"]) if pitcher_stat else LG["pitBBPct"]
    count_adv_mult = count_advantage(k_pct, bb_pct, pit_k_pct, pit_bb_pct)

    # Factor 16: Barrel estimate (ISO-derived -- dampened, see Factor 1)
    barrel = est_barrel(iso)
    barrel_mult = clamp(1 + 0.30 * (barrel / 0.078 - 1), 0.85, 1.30)

    # Factor 17: Zone match
    zone_score = 50 + (iso / LG["iso"] - 1) * 14 - (pit_k_pct / LG["pitKPct"] - 1) * 11
    if platoon:
        zone_score += 8
    zone_mult = clamp(0.87 + (zone_score / 50) * 0.13, 0.87, 1.13)

    # Factor 18: Hard contact -- centered so a league-average hard-hit rate
    # yields 1.0 (was 0.88 + 0.24*ratio = 1.12 at neutral, a free 12% boost
    # for everyone).
    hard_rate = est_hard_contact(slg, avg, xbh, pa)
    hard_mult = clamp(1 + 0.15 * (hard_rate / LG["hardHitPct"] - 1), 0.88, 1.15)

    # Factor 19: Pull tendency -- likewise recentered around 1.0.
    pull_rate = est_pull_rate(hr, ab)
    pull_mult = clamp(1 + 0.18 * (pull_rate / LG["pullPct"] - 1), 0.85, 1.18)

    # Factor 20: Pitcher fly-ball rate -- wired to the real groundout/airout
    # based estimate from pitcher_stat instead of a hard-coded 1.0.
    fb_mult = fb_factor(pitcher_stat)

    # Factor 21: Pitcher WHIP
    whip_mult = whip_factor(pitcher_stat)

    pit_trend_mult = 1.0

    # Factor 23: Slugging trend (L7 vs L14)
    slug_trend_mult = 1.0
    if batter_l7 and batter_l14:
        pa_l7 = batter_l7.get("pa", 0)
        pa_l14 = batter_l14.get("pa", 0)
        if pa_l7 >= 10 and pa_l14 >= 20:
            slg_l7 = batter_l7.get("slg", slg)
            slg_l14 = batter_l14.get("slg", slg)
            trend = (slg_l7 - slg_l14) / max(slg_l14, 0.001)
            slug_trend_mult = clamp(1 + trend * 0.55, 0.90, 1.12)

    # Factor 24: Lineup protection
    lineup_prot_mult = 1.0
    if 3 <= order <= 5:
        lineup_prot_mult = 1.04

    # Factor 25-29: Advanced placeholders (no reliable free data source yet)
    air_density_mult = 1.0
    umpire_mult = 1.0
    travel_fatigue_mult = 1.0
    catcher_framing_mult = 1.0

    # === NEW FACTORS (30-34) ===

    # Factor 30: K-rate trend
    k_recent_pct = batter_l7.get("k", 0) / batter_l7.get("pa", 1) if batter_l7 and batter_l7.get("pa", 0) >= 15 else k_pct
    k_trend_mult = k_rate_trend(k_recent_pct, k_pct, batter_l7.get("pa", 0) if batter_l7 else 0)

    # Factor 31: Sprint speed (inverse for power)
    sprint_mult = est_sprint_speed_boost(sb, pa)

    # Factor 32: Bullpen vulnerability
    bullpen_mult = bullpen_vulnerability(bullpen_hr9)

    # Factor 33: Pitcher fatigue (IP cumulative)
    pit_fatigue_mult = pitcher_fatigue_factor(pitcher_ip_season)

    # === CONVERGENCE BONUS ===
    strong_signals = 0
    factors_list = [
        iso_mult, platoon_mult, park_mult, wind_mult, temp_mult,
        h2h_mult, vs_mult, barrel_mult, zone_mult, hard_mult,
        pull_mult, slug_trend_mult, lineup_prot_mult, k_trend_mult,
        bullpen_mult, pit_fatigue_mult, pitcher_hr_mult, pitcher_k_mult,
        fb_mult, whip_mult, count_adv_mult, recent_form, streak7
    ]

    for f in factors_list:
        if f > 1.08 or f < 0.94:
            strong_signals += 1

    if strong_signals >= 7:
        convergence_mult = 1.10
    elif strong_signals >= 5:
        convergence_mult = 1.06
    elif strong_signals >= 3:
        convergence_mult = 1.03
    else:
        convergence_mult = 1.0

    # === COMBINE ALL FACTORS (pre-calibration) ===
    #
    # base_rate is a *per-plate-appearance* HR rate (LG hrPA ~= 0.031), so
    # the factor product yields a per-PA probability that must be converted
    # to a per-game probability over the batter's expected PAs. The old code
    # skipped that conversion entirely (lineup_pa() existed but was never
    # called) and published the per-PA number as "gameProb" -- which is why
    # tracked 5-10% picks actually homered ~15% of the time while the top
    # of the slate simultaneously pinned the 0.40 clamp.
    factor_product = (
        iso_mult * platoon_mult * park_mult * pitcher_hr_mult * pitcher_k_mult *
        wind_mult * temp_mult * precip_mult * season_mult *
        day_night_mult * h2h_mult * vs_mult * count_adv_mult *
        barrel_mult * zone_mult * hard_mult * pull_mult * fb_mult *
        pit_trend_mult * slug_trend_mult * lineup_prot_mult *
        air_density_mult * umpire_mult * travel_fatigue_mult *
        catcher_framing_mult * k_trend_mult * sprint_mult *
        bullpen_mult * pit_fatigue_mult *
        whip_mult * recent_form * streak7 * convergence_mult
    )
    # Square-root damping: ~30 individually-bounded factors still compound
    # multiplicatively, and many are positively correlated, so the raw
    # product routinely reached 4x+ for good hitters. Halving it in log
    # space keeps every factor's *direction* while restoring realistic
    # spread (a top slugger in a great spot lands ~25-30% per game, in line
    # with sportsbook HR props, instead of everything pinning a cap).
    per_pa = clamp(base_rate * (factor_product ** 0.5), 0.002, 0.075)
    exp_pa = lineup_pa(order)
    prelim_prob = clamp(1 - (1 - per_pa) ** exp_pa, 0.01, 0.30)

    # Factor 34: Self-calibration -- a global drift multiplier from recent
    # training_log.json accuracy, blended with a bucket-level (tiered)
    # correction learned from precision_k.py's tracked history. Both are
    # bounded and only engage once enough real evidence has accumulated.
    tier_mult = tier_calibration_mult(prelim_prob)
    self_calib_mult = clamp(GLOBAL_CALIBRATION_MULT * tier_mult, 0.88, 1.12)

    game_prob = clamp(prelim_prob * self_calib_mult, 0.01, 0.30)

    # Grading -- thresholds sit on the per-game scale: a league-average
    # regular lands ~12-13% per game, so A is reserved for genuinely
    # elite spots.
    grade = "D"
    if game_prob >= 0.20:
        grade = "A"
    elif game_prob >= 0.15:
        grade = "B"
    elif game_prob >= 0.11:
        grade = "C"

    return {
        "pid": pid,
        "name": name,
        "team": team,
        "gameProb": round(game_prob, 4),
        "perPA": round(per_pa, 4),
        "expPA": exp_pa,
        "gameMatchup": game_matchup,
        "battingOrder": order,
        "pitcherName": game.get("pitcher", ""),
        "grade": grade,
        "factors": {
            "iso": round(iso_mult, 3),
            "platoon": round(platoon_mult, 3),
            "park": round(park_mult, 3),
            "wind": round(wind_mult, 3),
            "temp": round(temp_mult, 3),
            "pitcherHR": round(pitcher_hr_mult, 3),
            "pitcherK": round(pitcher_k_mult, 3),
            "fb": round(fb_mult, 3),
            "whip": round(whip_mult, 3),
            "vsHand": round(vs_mult, 3),
            "h2h": round(h2h_mult, 3),
            "kTrend": round(k_trend_mult, 3),
            "recentFormL14": round(recent_form, 3),
            "streak7": round(streak7, 3),
            "bullpen": round(bullpen_mult, 3),
            "pitFatigue": round(pit_fatigue_mult, 3),
            "selfCalib": round(self_calib_mult, 3),
            "tierCalib": round(tier_mult, 3),
            "convergence": round(convergence_mult, 3)
        }
    }
