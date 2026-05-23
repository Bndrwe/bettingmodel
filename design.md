# HR Oracle v4 — Design Document

## Product Name & Tagline
**HR Oracle v4**  
*Matchup Intelligence Engine*

The title screen displays "HR ORACLE" with "ORACLE" rendered in `--green` (#00e887). The subtitle reads "MATCHUP INTELLIGENCE ENGINE v4".

## Purpose
HR Oracle v4 is a statistical baseball prediction tool that ranks batters by their probability of hitting a home run in today's MLB games. It processes ~300+ batters daily using a 34-factor model[web:12], displays the top picks with grade-based confidence ratings, and tracks self-calibrated accuracy metrics.

## Critical Files

### Frontend
- **index.html** — Single-page application (3178 lines)[web:10]
  - Embedded CSS with design tokens in `:root`
  - Vanilla JavaScript for data loading, rendering, and UI interactions
  - No external framework dependencies

### Backend (Python)
- **backend/factors.py** — Factor calculation engine (352 lines)[web:12]
  - Defines 34 factors (numbered 1–34, some placeholders)
  - Core logic: ISO multiplier, platoon advantage, park factors, weather, H2H history, barrel rate, K-rate trend, sprint speed, bullpen vulnerability, pitcher fatigue
  - Includes "Leverage Gauge" (LG) dict with performance thresholds
  - Convergence bonus: compounds when multiple factors signal strongly (outside 0.94–1.08 range)

- **backend/model.py** — Prediction pipeline (main prediction execution)

- **backend/precision_k.py** — Precision@10 accuracy tracker
  - Monitors performance of top-10 daily picks
  - Referenced in commit message: "feat: add Precision@10 (top-10) daily accuracy tracking"

- **backend/archive_season.py** — End-of-season cleanup utility

- **backend/requirements.txt** — Python dependencies

### Data
- **data/predictions.json** — Daily prediction output
  - Structure: `{updatedAt, season, predictions: []}`
  - Currently placeholder (empty predictions array)

### Documentation
- **PARLAY_ANALYZER.md** — OpenRouter AI integration guide[web:14]
  - Security model: Cloudflare Worker proxy pattern
  - API key stored as encrypted environment variable
  - Origin restriction: `https://bndrwe.github.io` only
  - Default model: `openai/gpt-3.5-turbo`

## Design System

### Color Palette (from index.html)[web:10]
```css
:root {
  /* Backgrounds */
  --bg: #080f1c;      /* Page background */
  --bg2: #0c1525;     /* Secondary background */
  --bg3: #111d30;     /* Tertiary background */
  
  /* Surfaces */
  --surface: #141f32;
  --surf2: #1a2840;
  --surf3: #1f3050;
  
  /* Borders */
  --border: #1e3050;
  --bord2: #274060;
  
  /* Accent Colors */
  --green: #00e887;   /* Primary brand color ("ORACLE") */
  --green2: #00b86d;
  --green3: rgba(0,232,135,0.12);
  --cyan: #22d3ee;
  --amber: #f59e0b;
  --amber2: #fbbf24;
  --red: #f43f5e;
  --purple: #a855f7;
  --blue: #3b82f6;
  
  /* Text */
  --text: #b8cfe0;    /* Primary text */
  --text2: #6a8ea8;   /* Secondary text */
  --text3: #38546a;   /* Tertiary text */
  --white: #e8f3ff;
  
  /* Special */
  --gold: #ffd166;
  --live: #ff4d6d;
}
```

### Typography
- Font: **Bebas Neue** (from Google Fonts)
- Weights: 400 (normal), 700 (bold)
- Secondary: **Space Mono** (monospace, weights 400/600)
- Inter font at 400/500/600 for body text

## Page Structure (from live site)[screenshot:20]

### Header
```
[HR ORACLE v4]        [↻ REFRESH] [HISTORY]    TODAY: 2026-05-23
                                                 UPDATED: 11:58:00 AM
```

### Stats Bar
```
GAMES TODAY: 15              PLAYERS RANKED: 343
```

### Section 1: 🔥 TOP 5 TONIGHT
- Card-based grid layout (5 cards)
- Each card shows:
  - **Rank badge**: "#1 PICK", "#2 PICK", etc.
  - **Grade circle**: Letter grade (A, B, C, D) with green stroke
  - **Player name**: Large white text
  - **Matchup**: "STL @ TOR" in small text with emoji
  - **Probability**: Large green percentage (e.g., "47.1%")
  - **Progress bar**: Green horizontal bar showing confidence
  - **Badges**: "🔥 HOT", "🏟️ HR PARK" labels
  - **Context**: "vs Corbin Burnes" in smaller text

### Section 2: 📊 MODEL ACCURACY & SELF-CALIBRATION
**Subtitle**: "LAST 30 DAYS · AUTO-TRACKED · FACTOR 29"

**Top metrics row** (5 boxes):
```
─%           2%            1%            18           1%
PRECISION@10  HIGH CONF.ACC  OVERALL ACC  TOTAL HRS HIT  MED CONF.ACC
Top 10 Picks  7/167 picks   9/683 picks  not tracked    9/683 picks
Elite
```

**Factor 29 status**:
```
🟢 FACTOR 29 — SELF-CALIBRATION ↓ OVER-PREDICTING
Observed 1.0% vs expected 20% · tier-bucket correction active    0.920×
```

**Calibration charts**:
- **HIGH (38%)**: 2% actual hit rate (GREEN)
- **MED (16-38%)**: 1% actual hit rate (RED)
- **ALL PICKS**: 1% overall

**Prob bucket calibration**: Horizontal bars showing expected vs actual for 5-10%, 10-15% ranges

**Factor reliability rankings**:
```
🏆 FACTOR RELIABILITY RANKING
Model Left (>7 months signal) · valid on ALL 303 picks

🔥 Convergence     +31.3 pts
🔥 Hard Contact    +31.2 pts   38%
```

### Section 3: 🎲 PARLAY BUILDER
(Interactive section for combining picks)

### Section 4: FULL RANKINGS
(Data grid with filtering)

## Navigation & Interactions

### Matchup Modal[web:10]
- Triggered by clicking player cards
- Shows: H2H stats, pitch mix, contact quality, field spray charts

### Betslip Drawer[web:10]
- Active tracking for selected props
- Result monitoring

## Data Model

### Predictions JSON Structure
From `data/predictions.json`:
```json
{
  "updatedAt": "2026-05-22",
  "season": 2026,
  "predictions": []
}
```

Expected full structure (from features):
```json
{
  "updatedAt": "YYYY-MM-DD",
  "season": 2026,
  "predictions": [
    {
      "player": "Brandon Lowe",
      "team": "STL",
      "opponent": "TOR",
      "hr_probability": 0.471,
      "grade": "A",
      "rank": 1,
      "pitcher": "Corbin Burnes",
      "badges": ["HOT", "HR PARK"]
    }
  ]
}
```

## Goals

### Primary Goal
**Identify high-probability home run hitters** for daily MLB games using multi-factor statistical analysis.

### Secondary Goals
1. **Track and display accuracy** — Self-calibrating model with Precision@10 and confidence-tier metrics
2. **Enable parlay analysis** — AI-powered risk assessment via OpenRouter integration
3. **Provide transparency** — Factor reliability rankings show which signals are working
4. **Maintain security** — No API keys in client-side code (Cloudflare Worker proxy)

### Success Metrics
- **Precision@10**: Percentage of top-10 daily picks that hit HR
- **Calibration accuracy**: Observed HR rate matches predicted probability buckets
- **Factor 29 (Self-Calibration)**: Auto-adjusts when over/under-predicting
- **Convergence bonus**: Reward for multiple strong signals aligning

## Technical Architecture

### Deployment
- **Hosting**: GitHub Pages
- **URL**: `https://bndrwe.github.io/hrmodel/`
- **Auto-deploy**: Commits to `main` branch trigger GitHub Pages rebuild

### Workflow
1. Python backend calculates 34 factors for ~300 batters
2. Model outputs predictions to `data/predictions.json`
3. Frontend fetches JSON and renders UI
4. Accuracy tracking compares predictions against real MLB results
5. Factor 29 applies correction multiplier if systematically off

### Parlay Analyzer Security Pattern[web:14]
```
Browser → Cloudflare Worker (proxy) → OpenRouter API
          ↑ API key stored here
          ↑ CORS: bndrwe.github.io only
```

## Factor List (34 total)[web:12]

1. ISO multiplier
2. Platoon advantage
3. Park factor (hand-specific)
4. Pitcher HR multiplier
5. Pitcher K multiplier
6. Weather wind
7. Temperature
8. Precipitation risk
9. Season phase
10. Lineup slot
11. Day/night
12. H2H history
13. Vs hand splits
14. Count advantage
15. Count advantage (Pitcher)
16. Barrel estimate
17. Zone match
18. Hard contact
19. Pull tendency
20. Pitcher trend (Placeholder)
21. FB% (Placeholder)
22. FB% (Placeholder)
23. Slugging trend
24. Lineup protection
25. Air density (Placeholder)
26. Umpire (Placeholder)
27. Travel fatigue (Placeholder)
28. Catcher framing (Placeholder)
29. **Self-calibration** (Factor 29 — active correction)
30. K-rate trend
31. Sprint speed
32. Bullpen vulnerability
33. Pitcher fatigue
34. Advanced placeholder

**Plus**: Convergence bonus (compounds when multiple factors signal strongly)

## Terminology

- **Precision@10**: Accuracy of top 10 daily picks
- **Leverage Gauge (LG)**: Performance threshold dictionary in factors.py
- **Convergence**: Bonus multiplier when factors align (outside 0.94–1.08)
- **Factor 29**: Self-calibration system that adjusts for systematic over/under-prediction
- **Grade**: A/B/C/D confidence rating for each pick
- **HR Park**: Badge indicating ballpark favors home runs
- **Hot**: Badge indicating recent strong performance
- **Matchup Intelligence**: Core value proposition (analyzing batter vs pitcher dynamics)

---

**Version**: 4  
**Last Updated**: Based on codebase as of January 2025  
**Document Status**: Generated from actual repository files and deployed website
