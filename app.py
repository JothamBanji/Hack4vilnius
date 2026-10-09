"""
STEP 3 - The website.   Run:  streamlit run app.py
"""
from pathlib import Path

import pandas as pd
import pydeck as pdk
import streamlit as st

from analysis import DEFAULT_SEVERITY, monthly_trend, score_hotspots

st.set_page_config(page_title="Vilnius Pulse", page_icon="📍", layout="wide")


@st.cache_data
def load():
    return pd.read_parquet(Path(__file__).parent / "data" / "reports.parquet")


df = load()
all_groups = sorted(df["group"].astype(str).unique())

# ---------------- Sidebar: controls ----------------
st.sidebar.title("📍 Vilnius Pulse")
st.sidebar.caption("Which city problems should be fixed first, and why.")

max_day = df.created.max().date()
as_of = st.sidebar.date_input("Analyse up to", value=max_day,
                              min_value=df.created.min().date(), max_value=max_day)
window = st.sidebar.select_slider("Look-back window (days)", [30, 60, 90, 180, 365], value=90)
default_groups = [g for g in all_groups if g != "Parking violations"]
groups = st.sidebar.multiselect("Problem types", all_groups, default=default_groups,
                                help="Parking violations are hidden by default: they are an "
                                     "enforcement issue and would otherwise drown everything else.")
cell_m = st.sidebar.select_slider("Hotspot size (metres)", [100, 150, 250, 400], value=150)
min_rep = st.sidebar.slider("Min. different residents per hotspot", 1, 10, 2)

with st.sidebar.expander("⚖️ How serious is each problem? (weights)"):
    severity = {g: st.slider(g, 0.0, 2.0, float(DEFAULT_SEVERITY.get(g, 1.0)), 0.1)
                for g in groups}

# ---------------- Compute ----------------
as_of_ts = pd.Timestamp(as_of) + pd.Timedelta(days=1)
data = df[df["group"].isin(groups)]
hs = score_hotspots(data, as_of_ts, window, severity, cell_m, min_rep)

st.title("Vilnius Pulse — city problem radar")
st.caption(f"Resident reports from Tvarkau miestą · last {window} days up to {as_of} · "
           f"{len(data):,} reports in the full history for the selected types")

if hs.empty:
    st.warning("No hotspots for these settings. Try a longer window or fewer minimum residents.")
    st.stop()

recent = data[(data.created > as_of_ts - pd.Timedelta(days=window)) & (data.created <= as_of_ts)]
c1, c2, c3, c4 = st.columns(4)
c1.metric("Reports in window", f"{len(recent):,}")
c2.metric("Hotspots found", f"{len(hs):,}")
c3.metric("Growing or new", f"{hs.status_tag.isin(['GROWING', 'NEW']).sum():,}")
c4.metric("Still unresolved", f"{int(recent.is_open.sum()):,}")

# ---------------- Map + Top 10 ----------------
left, right = st.columns([3, 2])

with left:
    top_n = st.slider("Show top N hotspots on map", 10, 500, 100)
    m = hs.head(top_n).copy()
    m["group"] = m["group"].astype(str)
    m["color"] = m["score"].apply(lambda s: [255, int(200 - 1.8 * s), 40, 200])
    m["radius"] = 40 + m["score"] * 2.5
    layer = pdk.Layer("ScatterplotLayer", m, get_position=["cell_lon", "cell_lat"],
                      get_fill_color="color", get_radius="radius", pickable=True)
    st.pydeck_chart(pdk.Deck(
        layers=[layer],
        initial_view_state=pdk.ViewState(latitude=54.69, longitude=25.28, zoom=11),
        tooltip={"html": "<b>{group}</b> · score {score} · {status_tag}<br/>{address}"
                         "<br/><br/>{reason}",
                 "style": {"maxWidth": "320px", "fontSize": "12px"}}))

with right:
    st.subheader("🔥 Top 10 — fix these first")
    tag_icon = {"NEW": "🆕", "GROWING": "📈", "RECURRING": "🔁", "FADING": "📉", "STABLE": "➖"}
    for i, r in hs.head(10).iterrows():
        st.markdown(f"**#{i + 1} · {r.group}** — score **{r.score}** "
                    f"{tag_icon.get(r.status_tag, '')} {r.status_tag}  \n"
                    f"📍 {r.address}  \n"
                    f"<span style='color:gray;font-size:0.9em'>{r.reason}</span>",
                    unsafe_allow_html=True)
        if r.example:
            with st.expander("What a resident wrote"):
                st.write(r.example)

# ---------------- Trends ----------------
st.subheader("📊 How problems change over time (reports per month)")
trend = monthly_trend(data, groups)
st.line_chart(trend)

st.subheader("All hotspots")
cols = ["score", "status_tag", "group", "address", "reports", "reporters", "prev_reports",
        "months_active", "still_open", "median_days_to_close", "reason"]
st.dataframe(hs[cols], width="stretch", hide_index=True)
st.download_button("⬇️ Download as CSV", hs[cols].to_csv(index=False).encode("utf-8"),
                   "vilnius_hotspots.csv", "text/csv")

with st.expander("ℹ️ How the score works"):
    st.markdown("""
Each hotspot is a ~150 m square + one problem type. Its score (0–100) grows when:
- **more different residents** report it (one person reporting 50 times counts less than 10 people),
- it is **getting worse** compared with the previous period,
- it **keeps coming back** month after month,
- reports are **still unresolved**,
- the problem type is **more serious** (adjustable weights in the sidebar).
""")
