"""
STEP 1 - Clean the big Tvarkau miestą CSV into a small, fast file.

Run once:   python prepare.py
Input:      tvarkau_miesta_reports.csv   (the 570 MB download, same folder)
Output:     data/reports.parquet         (~20-40 MB, small enough for GitHub)
"""
import sys
from pathlib import Path
import pandas as pd

INPUT = sys.argv[1] if len(sys.argv) > 1 else "tvarkau_miesta_reports.csv"
OUTPUT = Path(__file__).parent / "data" / "reports.parquet"

# Only the columns we need (the long "report_answer" text is skipped to save memory)
COLS = ["id", "report_type", "report_registration_date", "report_completion_date",
        "lattitude", "longitude", "report_address", "status", "division_name",
        "user_id_hash", "report_description"]

# Lithuanian category  ->  simple English group (used everywhere in the app)
GROUPS = [
    ("Playgrounds & sports",    ["žaidimo aikštel", "sporto aikštel"]),
    ("Parking violations",      ["stovėjimo tvarkos"]),
    ("Abandoned vehicles",      ["neeksploatuojami automobiliai"]),
    ("Potholes & road damage",  ["duobės", "gatvių remontas", "žvyrkeliai", "kiemų dangos", "tilt"]),
    ("Sidewalks & stairs",      ["šaligatvių", "laiptai"]),
    ("Traffic safety",          ["eismo organizavimas", "ženklai", "šviesofor", "dviračių tak"]),
    ("Street lighting",         ["apšvietim"]),
    ("Litter & waste",          ["atliek", "konteiner", "švaros", "aplinkos tvarkymas", "grafi",
                                 "teritorijų tvarkymas", "valym", "šiukšliadėž", "suoleli", "sezoninė dangų"]),
    ("Trees & greenery",        ["želdyn", "medž", "žol"]),
    ("Buildings",               ["pastat", "statini"]),
    ("Water, heat & utilities", ["vanden", "šilum", "nuotek", "energetik", "inžinerini"]),
    ("Public transport",        ["viešasis transport", "viešojo transporto", "stotel"]),
    ("Noise",                   ["triukšm"]),
    ("Animals",                 ["gyvūn"]),
]


def to_group(category: str) -> str:
    c = str(category).lower()
    for group, keywords in GROUPS:
        if any(k in c for k in keywords):
            return group
    return "Other"


def main():
    print(f"Reading {INPUT} ... (this can take 1-3 minutes)")
    parts = []
    for i, chunk in enumerate(pd.read_csv(INPUT, usecols=lambda c: c in COLS,
                                          chunksize=200_000, on_bad_lines="skip")):
        parts.append(chunk)
        print(f"  read {sum(len(p) for p in parts):,} rows")
    df = pd.concat(parts, ignore_index=True)

    df = df.rename(columns={"lattitude": "lat", "longitude": "lon",
                            "report_type": "category",
                            "report_registration_date": "created",
                            "report_completion_date": "completed",
                            "report_address": "address", "user_id_hash": "reporter"})
    df["created"] = pd.to_datetime(df["created"], format="%Y/%m/%d %H:%M:%S", errors="coerce")
    df["completed"] = pd.to_datetime(df["completed"], format="%Y/%m/%d %H:%M:%S", errors="coerce")

    # Keep only rows with a date and a location inside the Vilnius area
    df = df.dropna(subset=["created", "lat", "lon"])
    df = df[df.lat.between(54.55, 54.85) & df.lon.between(25.0, 25.5)]

    df["group"] = df["category"].map(to_group)
    df["is_open"] = df["status"].ne("Išnagrinėta")
    df["days_to_close"] = (df["completed"] - df["created"]).dt.days

    # Anonymised texts carry no info - blank them; shorten the rest
    desc = df["report_description"].fillna("")
    df["text"] = desc.where(desc.ne("Duomenys nuasmeninti"), "").str.slice(0, 300)
    df = df.drop(columns=["report_description"])

    for c in ["category", "group", "status", "division_name"]:
        df[c] = df[c].astype("category")

    # Drop helper columns we can recompute, then save compressed
    df = df.drop(columns=["completed"])
    OUTPUT.parent.mkdir(exist_ok=True)
    df.to_parquet(OUTPUT, compression="zstd", index=False)
    print(f"Saved {OUTPUT}  ({OUTPUT.stat().st_size / 1e6:.1f} MB)")
    print(f"\nDone: {len(df):,} reports from {df.created.min():%Y-%m-%d} to {df.created.max():%Y-%m-%d}")
    print("\nReports per group:")
    print(df["group"].value_counts().to_string())
    unmatched = df.loc[df.group == "Other", "category"].value_counts()
    unmatched = unmatched[unmatched > 0].head(15)
    if len(unmatched):
        print("\nTop categories that fell into 'Other' (add keywords in GROUPS if needed):")
        print(unmatched.to_string())


if __name__ == "__main__":
    main()
