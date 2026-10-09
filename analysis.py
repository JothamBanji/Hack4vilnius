"""
STEP 2 - The "brain": turns raw reports into ranked hotspots.

A hotspot = one small square of the city (about 150 m x 150 m) + one problem group.
Each hotspot gets a priority score from 0 to 100, plus a plain-language reason.
"""
import math
import numpy as np
import pandas as pd

# How serious each problem group is (1.0 = normal). Adjustable in the app.
DEFAULT_SEVERITY = {
    "Traffic safety": 1.5, "Potholes & road damage": 1.3, "Sidewalks & stairs": 1.2,
    "Street lighting": 1.2, "Water, heat & utilities": 1.2, "Public transport": 1.1,
    "Litter & waste": 1.0, "Playgrounds & sports": 1.0, "Noise": 1.0, "Buildings": 0.9,
    "Trees & greenery": 0.8, "Abandoned vehicles": 0.8, "Other": 0.8,
    "Parking violations": 0.6,
}


def add_cells(df: pd.DataFrame, cell_m: int = 150) -> pd.DataFrame:
    """Snap every report to a grid square of roughly cell_m metres."""
    lat_step = cell_m / 111_320
    lon_step = cell_m / (111_320 * math.cos(math.radians(54.69)))
    df = df.copy()
    df["cy"] = np.floor(df["lat"] / lat_step).astype(int)
    df["cx"] = np.floor(df["lon"] / lon_step).astype(int)
    df["cell_lat"] = (df["cy"] + 0.5) * lat_step
    df["cell_lon"] = (df["cx"] + 0.5) * lon_step
    return df


def best_address(s: pd.Series) -> str:
    """Most common address that actually names a street (some are just ', LT-01141 Vilnius')."""
    s = s.dropna().astype(str).str.strip()
    good = s[~s.str.startswith(",") & (s.str.len() > 12)]
    pick = good if len(good) else s
    return pick.mode().iat[0] if len(pick) else ""


def score_hotspots(df: pd.DataFrame, as_of: pd.Timestamp, window_days: int = 90,
                   severity: dict | None = None, cell_m: int = 150,
                   min_reporters: int = 2) -> pd.DataFrame:
    """Rank every (square, problem group) by urgency."""
    severity = severity or DEFAULT_SEVERITY
    df = add_cells(df, cell_m)
    key = ["cy", "cx", "group"]

    w = pd.Timedelta(days=window_days)
    recent = df[(df.created > as_of - w) & (df.created <= as_of)]
    prev = df[(df.created > as_of - 2 * w) & (df.created <= as_of - w)]
    year = df[(df.created > as_of - pd.Timedelta(days=365)) & (df.created <= as_of)]

    g = recent.groupby(key, observed=True)
    hs = g.agg(reports=("id", "size"),
               reporters=("reporter", "nunique"),
               still_open=("is_open", "sum"),
               cell_lat=("cell_lat", "first"),
               cell_lon=("cell_lon", "first"),
               address=("address", best_address),
               example=("text", lambda s: next((t for t in s if t), "")),
               ).reset_index()
    hs = hs[hs.reporters >= min_reporters]
    if hs.empty:
        return hs

    hs = hs.merge(prev.groupby(key, observed=True).size().rename("prev_reports").reset_index(),
                  on=key, how="left")
    months = (year.assign(m=year.created.dt.to_period("M"))
                  .groupby(key, observed=True)["m"].nunique().rename("months_active").reset_index())
    hs = hs.merge(months, on=key, how="left")
    close = (df.dropna(subset=["days_to_close"]).groupby(key, observed=True)["days_to_close"]
               .median().rename("median_days_to_close").reset_index())
    hs = hs.merge(close, on=key, how="left")
    hs[["prev_reports", "months_active"]] = hs[["prev_reports", "months_active"]].fillna(0)

    # ---- The priority formula (simple on purpose, so the city can trust it) ----
    hs["trend"] = (hs.reports + 1) / (hs.prev_reports + 1)                  # >1 = getting worse
    volume = np.log1p(hs.reporters) + 0.3 * np.log1p(hs.reports)            # many PEOPLE > one person spamming
    trend_f = hs.trend.clip(0.5, 3) ** 0.5
    recur_f = 1 + hs.months_active / 12                                     # keeps coming back
    open_f = 1 + hs.still_open / hs.reports                                 # not yet solved
    sev = hs.group.astype(str).map(severity).fillna(1.0)
    raw = sev * volume * trend_f * recur_f * open_f
    hs["score"] = (100 * raw / raw.max()).round(0).astype(int)

    hs["status_tag"] = np.select(
        [(hs.prev_reports == 0) & (hs.reports >= 3),
         (hs.trend >= 1.5) & (hs.reports >= 5),
         hs.months_active >= 8,
         (hs.trend <= 0.67) & (hs.prev_reports >= 5)],
        ["NEW", "GROWING", "RECURRING", "FADING"], default="STABLE")
    hs["reason"] = hs.apply(lambda r: explain(r, window_days), axis=1)
    return hs.sort_values("score", ascending=False).reset_index(drop=True)


def explain(r, window_days: int) -> str:
    """One readable sentence per hotspot - this is what decision-makers see."""
    parts = [f"{int(r.reports)} reports from {int(r.reporters)} different residents "
             f"in the last {window_days} days"]
    if r.prev_reports == 0:
        parts.append("none in the period before")
    elif r.trend >= 1.5:
        parts.append(f"up {r.trend:.1f}x vs the previous {window_days} days")
    elif r.trend <= 0.67:
        parts.append("decreasing")
    if r.months_active >= 6:
        parts.append(f"reported in {int(r.months_active)} of the last 12 months (recurring)")
    if r.still_open:
        parts.append(f"{int(r.still_open)} still unresolved")
    if pd.notna(r.median_days_to_close):
        parts.append(f"usually takes {int(r.median_days_to_close)} days to close")
    return "; ".join(parts) + "."


def monthly_trend(df: pd.DataFrame, groups=None) -> pd.DataFrame:
    """Reports per month per group - for the 'how is it changing' chart."""
    d = df if groups is None else df[df.group.isin(groups)]
    t = (d.assign(month=d.created.dt.to_period("M").dt.to_timestamp())
           .groupby(["month", "group"], observed=True).size().unstack(fill_value=0))
    return t
