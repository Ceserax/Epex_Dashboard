import time
import re
from datetime import datetime, date, timedelta, time as dtime
from io import StringIO

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
import urllib3
import altair as alt
from zoneinfo import ZoneInfo

# ----------------- Config -----------------

# Bekende EPEX Day-Ahead marktgebieden (bidding zones)
EPEX_DAYAHEAD_MARKET_AREAS = [
    "NL",
    "BE",
    "DE-LU",
    "AT",
    "FR",
    "CH",
    "GB",
    "PL",
    "DK1",
    "DK2",
    "FI",
    "NO1", "NO2", "NO3", "NO4", "NO5",
    "SE1", "SE2", "SE3", "SE4",
]


# ----------------- EPEX scraping helpers -----------------

def check_table_presence(
    url: str,
    params: dict,
    verify_ssl: bool = True,
    max_retries: int = 5,
    retry_delay: int = 10,
) -> pd.DataFrame | None:
    """
    Haalt de eerste HTML-tabel van EPEX Spot op en geeft deze terug als DataFrame.
    Sterk vereenvoudigde versie voor dashboard-gebruik.
    """
    attempt = 0

    while attempt < max_retries:
        try:
            if not verify_ssl:
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9,nl;q=0.8",
            }

            resp = requests.get(
                url,
                params=params,
                headers=headers,
                verify=verify_ssl,
                timeout=30,
            )

            if resp.status_code == 403:
                attempt += 1
                if attempt < max_retries:
                    time.sleep(retry_delay)
                continue

            if resp.status_code != 200:
                attempt += 1
                if attempt < max_retries:
                    time.sleep(retry_delay)
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.find("table")
            if not table:
                attempt += 1
                if attempt < max_retries:
                    time.sleep(retry_delay)
                continue

            df = pd.read_html(StringIO(str(table)))[0]

            # MultiIndex afvlakken indien nodig
            if isinstance(df.columns, pd.MultiIndex):
                try:
                    while isinstance(df.columns, pd.MultiIndex) and df.columns.nlevels > 1:
                        df.columns = df.columns.droplevel(0)
                except Exception:
                    df.columns = [
                        " ".join([str(x) for x in c if str(x) != "nan"]).strip()
                        if isinstance(c, tuple)
                        else str(c)
                        for c in df.columns
                    ]

            # Kolomnamen opschonen
            df.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in df.columns]
            return df

        except requests.exceptions.RequestException:
            attempt += 1
            if attempt < max_retries:
                time.sleep(retry_delay)
            continue

    return None


def detect_price_column(df_raw: pd.DataFrame) -> str:
    """Zoek een kolom met prijzen (€/MWh) in een EPEX-tabel."""
    price_col_candidates = [
        c for c in df_raw.columns
        if re.search(r"price.*€/MWh|price|\(€/MWh\)", c, re.I)
    ]
    if price_col_candidates:
        return price_col_candidates[0]

    num_cols = [c for c in df_raw.columns if pd.api.types.is_numeric_dtype(df_raw[c])]
    if not num_cols:
        raise ValueError("Geen prijs-kolom gevonden in de EPEX-tabel.")
    return num_cols[-1]


def standardize_quarter_frame(df_raw: pd.DataFrame) -> pd.DataFrame:
    """
    Maakt van een EPEX 15-min tabel een uniform frame:
    kolommen: ['q', 'price'], q = 1..96
    Probeert automatisch tijd- en prijs-kolom te herkennen.
    """
    df = df_raw.copy()

    # Kandidaten tijdkolom
    time_col_candidates = [
        c for c in df.columns
        if re.search(r"(time|delivery|period|quarter)", c, re.I)
    ]
    if not time_col_candidates:
        time_col = df.columns[0]
    else:
        time_col = sorted(
            time_col_candidates,
            key=lambda c: (not re.search(r"(time|delivery)", c, re.I), len(c)),
        )[0]

    price_col = detect_price_column(df)

    def parse_start_time(x):
        s = str(x).strip()
        s = s.replace("–", "-")  # lang streepje → normaal streepje
        s = s.split("-")[0].strip()
        for fmt in ("%H:%M", "%H:%M:%S"):
            try:
                return datetime.strptime(s, fmt).time()
            except ValueError:
                continue
        return None

    times = df[time_col].map(parse_start_time)

    if times.isnull().all():
        # Als dit mislukt, gewoon 1..N genereren
        q = pd.Series(range(1, len(df) + 1))
        prices = pd.to_numeric(df[price_col], errors="coerce")
        df_std = pd.DataFrame({"q": q, "price": prices})
    else:
        def time_to_q(t: dtime) -> int:
            return t.hour * 4 + (t.minute // 15) + 1

        df_std = pd.DataFrame(
            {
                "timestamp_start": times,
                "q": times.map(time_to_q),
                "price": pd.to_numeric(df[price_col], errors="coerce"),
            }
        )

    df_std = df_std.dropna(subset=["q", "price"])
    df_std = df_std[(df_std["q"] >= 1) & (df_std["q"] <= 96)].copy()
    df_std["q"] = df_std["q"].astype(int)
    df_std = df_std.sort_values("q").reset_index(drop=True)

    return df_std[["q", "price"]]


def standardize_hourly_to_quarter(df_raw: pd.DataFrame) -> pd.DataFrame:
    """
    Zet een 60-min Day-Ahead tabel om naar kwartieren:
    - vindt de prijskolom
    - herhaalt elke uur-prijs 4x zodat q=1..96.
    """
    df = df_raw.copy()
    price_col = detect_price_column(df)

    prices = pd.to_numeric(df[price_col], errors="coerce").tolist()
    rows = []
    for hour_idx, p in enumerate(prices):
        if pd.isna(p):
            continue
        # q's voor dit uur: 1..4, 5..8, ...
        base_q = hour_idx * 4
        for offset in range(4):
            q = base_q + offset + 1
            if 1 <= q <= 96:
                rows.append({"q": q, "price": p})

    df_std = pd.DataFrame(rows)
    return df_std[["q", "price"]]


def fetch_epex_table(
    delivery_date: date,
    market_area: str,
    product_minutes: int,
) -> pd.DataFrame | None:
    """
    Haalt ruwe EPEX Day-Ahead tabel op voor een gegeven product (15 of 60).
    """
    url = "https://www.epexspot.com/en/market-data"

    params = {
        "data_mode": "table",
        "modality": "Auction",
        "sub_modality": "DayAhead",
        "market_area": market_area,
        "delivery_date": delivery_date.strftime("%Y-%m-%d"),
        "product": str(product_minutes),
        "auction": "MRC",
    }

    df_raw = check_table_presence(url, params, max_retries=5, retry_delay=5)
    if df_raw is None or df_raw.empty:
        return None

    # Kolomnaam harmoniseren als die exact zo heet
    if "Price (€/MWh)" in df_raw.columns and "price" not in df_raw.columns:
        df_raw = df_raw.rename(columns={"Price (€/MWh)": "price"})

    return df_raw


@st.cache_data(show_spinner=False)
def get_epex_quarter_prices(delivery_date: date, market_area: str) -> pd.DataFrame:
    """
    Haalt EPEX Spot Day-Ahead prijzen op als kwartierprofiel (q=1..96) voor
    een gegeven leveringsdatum en marktgebied.

    1) Probeert product=15 (echte 15-min MTU).
    2) Als dat niets oplevert, probeert product=60 (uurprijzen) en schaalt die
       op naar 96 kwartieren.
    """
    # 1) Eerst 15-min proberen
    df_raw_15 = fetch_epex_table(delivery_date, market_area, product_minutes=15)
    if df_raw_15 is not None and not df_raw_15.empty:
        try:
            df_q = standardize_quarter_frame(df_raw_15)
            return df_q
        except Exception:
            pass  # val terug op 60-min

    # 2) Fallback: 60-min -> naar kwartieren
    df_raw_60 = fetch_epex_table(delivery_date, market_area, product_minutes=60)
    if df_raw_60 is not None and not df_raw_60.empty:
        try:
            df_q = standardize_hourly_to_quarter(df_raw_60)
            return df_q
        except Exception:
            pass

    # Niets gevonden
    return pd.DataFrame(columns=["q", "price"])


# ----------------- Streamlit dashboard -----------------

st.set_page_config(
    page_title="EPEX Spot Day-Ahead 15-min/60-min Dashboard (Alle landen)",
    layout="wide",
)

st.title("EPEX Spot Day-Ahead Dashboard – Alle beschikbare landen")
st.caption(
    "Bron: openbare tabel op epexspot.com (scraping, niet officieel API-geborgd). "
    "Per marktgebied wordt eerst 15-min Day-Ahead geprobeerd; als dat niet lukt, "
    "worden 60-min prijzen naar 96 kwartieren opgeschaald."
)
tz = ZoneInfo("Europe/Amsterdam")
now = datetime.now(tz)
today = date.today()
tomorrow = today + timedelta(days=1)

# Datumkeuze
selected_date = st.date_input(
    "Kies leveringsdatum",
    value=tomorrow if datetime.now().hour >= 13 else today,
    min_value=today - timedelta(days=7),
    max_value=tomorrow,
    help=(
        "Morgen werkt zodra de EPEX day-ahead tabel gepubliceerd is "
        "(rond 12:45-13:15)."
    ),
)

with st.spinner(f"Prijzen ophalen voor leveringsdag {selected_date}..."):
    all_frames: list[pd.DataFrame] = []
    skipped_markets: list[str] = []

    for ma in EPEX_DAYAHEAD_MARKET_AREAS:
        df_ma = get_epex_quarter_prices(selected_date, ma)
        if df_ma is None or df_ma.empty:
            skipped_markets.append(ma)
            continue
        df_ma = df_ma.copy()
        df_ma["market_area"] = ma
        all_frames.append(df_ma)

if not all_frames:
    st.error(
        "Geen Day-Ahead prijzen gevonden voor deze datum in de gekozen marktgebieden. "
        "Mogelijk nog niet gepubliceerd of layout EPEX gewijzigd."
    )
    st.stop()

df_all = pd.concat(all_frames, ignore_index=True)
df_all["market_area"] = df_all["market_area"].astype("string")
df_all["q"] = df_all["q"].astype(int)

available_markets = sorted(df_all["market_area"].unique())

st.subheader("Selectie marktgebieden")
selected_markets = st.multiselect(
    "Marktgebieden voor vergelijking (klik in de legenda om lijnen in/uit te schakelen):",
    options=available_markets,
    default=available_markets,
)

if not selected_markets:
    st.warning("Selecteer minimaal één marktgebied.")
    st.stop()

plot_df = df_all[df_all["market_area"].isin(selected_markets)].copy()

# ---- Kerncijfers per marktgebied ----
st.subheader("Kerncijfers per marktgebied (Day-Ahead, als kwartieren)")

kpi_rows = []
for ma in selected_markets:
    df_ma = plot_df[plot_df["market_area"] == ma]
    if df_ma.empty:
        continue
    baseload = df_ma["price"].mean()
    std_dev = df_ma["price"].std(ddof=0)
    min_price = df_ma["price"].min()
    max_price = df_ma["price"].max()
    peak_mask = df_ma["q"].between(33, 80)  # 09:00–20:00
    peakload = df_ma.loc[peak_mask, "price"].mean() if peak_mask.any() else float("nan")

    kpi_rows.append(
        {
            "Marktgebied": ma,
            "Baseload (gem.) [€/MWh]": round(baseload, 2),
            "Peakload 09–20 [€/MWh]": round(peakload, 2) if not pd.isna(peakload) else None,
            "Min [€/MWh]": round(min_price, 2),
            "Max [€/MWh]": round(max_price, 2),
            "Std. dev. [€/MWh]": round(std_dev, 2),
        }
    )

kpi_df = pd.DataFrame(kpi_rows)
st.dataframe(kpi_df, use_container_width=True)

# ---- Grafiek: prijsprofiel per kwartier voor alle markten ----
st.subheader("Prijsprofiel per kwartier – vergelijking tussen marktgebieden")

# Data voor verticale lijnen
quarter_lines_data = pd.DataFrame({"q": list(range(1, 97))})
hour_lines_data = pd.DataFrame(
    {"q_hour": [h * 4 for h in range(1, 25) if h * 4 <= 96]}
)

# Lichtgrijze lijnen voor elk kwartier
quarter_lines = (
    alt.Chart(quarter_lines_data)
    .mark_rule(strokeWidth=0.5, color="#eeeeee")
    .encode(x="q:Q")
)

# Donkerdere lijnen voor elk uur
hour_lines = (
    alt.Chart(hour_lines_data)
    .mark_rule(strokeWidth=1, color="#bbbbbb")
    .encode(x="q_hour:Q")
)

# Interactieve selectie via legenda – nieuwe Altair 5 stijl (add_params)
selection = alt.selection_point(fields=["market_area"], bind="legend", toggle="true")

base = alt.Chart(plot_df).encode(
    x=alt.X(
        "q:Q",
        title="Kwartier van de dag (1–96) – lichte lijnen = kwartieren, donkere lijnen = uren",
        scale=alt.Scale(domain=(1, 96)),
        axis=alt.Axis(
            values=[h * 4 for h in range(1, 25) if h * 4 <= 96],
            labelExpr="(datum.value / 4) + 'h'",
            labelAngle=0,
        ),
    ),
    y=alt.Y("price:Q", title="Prijs (€/MWh)"),
    color=alt.Color(
        "market_area:N",
        title="Marktgebied",
        legend=alt.Legend(title="Marktgebied (klik om te filteren)"),
    ),
    tooltip=[
        alt.Tooltip("market_area:N", title="Markt"),
        alt.Tooltip("q:Q", title="Kwartier"),
        alt.Tooltip("price:Q", title="Prijs (€/MWh)", format=".2f"),
    ],
)

lines = (
    base.mark_line(interpolate="monotone", strokeWidth=2)
    .encode(
        opacity=alt.condition(selection, alt.value(1.0), alt.value(0.2)),
    )
    .add_params(selection)  # i.p.v. .add_selection(selection)
)

chart = (quarter_lines + hour_lines + lines).properties(
    width="container",
    height=450,
)

# Streamlit nieuwe API: width="stretch" i.p.v. use_container_width
st.altair_chart(chart, width="stretch")

# ---- Uitleg ----
st.markdown(
    """
**Legenda / verklaring:**

- Elke **kleur** staat voor een ander *marktgebied* op EPEX Spot.  
- De **lichte verticale lijnen** geven elke 15-minuutperiode (kwartier) aan.  
- De **donkere verticale lijnen** markeren de **uren** (1h, 2h, ..., 24h), en deze uren staan ook op de x-as.  
- Door in de **legenda** op een marktgebied te klikken kun je lijnen verbergen of juist accentueren.  
- Als een markt alleen uurprijzen heeft, worden die **naar 96 kwartieren verdeeld** (zelfde prijs in de 4 kwartieren van dat uur).
"""
)

# ---- Download knop ----
st.subheader("Download data")

csv_data = df_all.to_csv(index=False)
st.download_button(
    "Download alle 15-minuut-equivalente prijzen (CSV) voor geselecteerde datum",
    data=csv_data,
    file_name=f"EPEX_DayAhead_quarters_all_markets_{selected_date}.csv",
    mime="text/csv",
)

if skipped_markets:
    st.caption(
        "Voor de volgende marktgebieden kon geen Day-Ahead tabel worden gevonden "
        "(geen publieke data of andere productstructuur): "
        + ", ".join(skipped_markets)
    )

st.caption(
    "Let op: dit dashboard scrapt de publieke EPEX Spot marktdatapagina. "
    "Als EPEX de HTML-structuur of productspecificaties wijzigt, moet de parser mogelijk worden aangepast."
)
