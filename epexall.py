from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import altair as alt
from entsoe import EntsoePandasClient
from entsoe.mappings import Area

# ----------------- Config -----------------

# Entso-e API Key
ENTSOE_API_KEY = "88f62dd9-0372-434c-8bc8-52b3c36a127f"

# Bekende Day-Ahead marktgebieden (bidding zones)
ENTSOE_MARKET_AREAS = [
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

# Mapping van display namen naar Entso-e Area codes
MARKET_AREA_TO_ENTSOE = {
    "NL": Area.NL,
    "BE": Area.BE,
    "DE-LU": Area.DE_LU,
    "AT": Area.AT,
    "FR": Area.FR,
    "CH": Area.CH,
    "GB": Area.GB,
    "PL": Area.PL,
    "DK1": Area.DK_1,
    "DK2": Area.DK_2,
    "FI": Area.FI,
    "NO1": Area.NO_1,
    "NO2": Area.NO_2,
    "NO3": Area.NO_3,
    "NO4": Area.NO_4,
    "NO5": Area.NO_5,
    "SE1": Area.SE_1,
    "SE2": Area.SE_2,
    "SE3": Area.SE_3,
    "SE4": Area.SE_4,
}


# ----------------- Entso-e API helpers -----------------

def convert_hourly_to_quarters(prices_series: pd.Series) -> pd.DataFrame:
    """
    Converteert uurlijkse prijzen naar kwartieren door forward fill.
    Elke uurprijs wordt herhaald voor 4 kwartieren (q=1..96).
    """
    rows = []
    for hour_idx, (timestamp, price) in enumerate(prices_series.items()):
        if pd.isna(price):
            continue
        # Elk uur heeft 4 kwartieren
        base_q = hour_idx * 4
        for offset in range(4):
            q = base_q + offset + 1
            if 1 <= q <= 96:
                rows.append({"q": q, "price": float(price)})
    
    return pd.DataFrame(rows)


def convert_quarters_to_standardized(prices_series: pd.Series) -> pd.DataFrame:
    """
    Converteert 15-minuut prijzen naar gestandaardiseerd formaat.
    """
    rows = []
    for idx, (timestamp, price) in enumerate(prices_series.items()):
        if pd.isna(price):
            continue
        q = idx + 1
        if 1 <= q <= 96:
            rows.append({"q": q, "price": float(price)})
    
    return pd.DataFrame(rows)


@st.cache_data(show_spinner=False)
def get_day_ahead_prices(delivery_date: date, market_area: str) -> pd.DataFrame:
    """
    Haalt Day-Ahead prijzen op van Entso-e Transparency Platform als 
    kwartierprofiel (q=1..96) voor een gegeven leveringsdatum en marktgebied.
    
    Als de data hourly is (24 punten), wordt deze naar 96 kwartieren geconverteerd
    door forward fill.
    """
    try:
        # Initialiseer de Entso-e client
        client = EntsoePandasClient(api_key=ENTSOE_API_KEY)
        
        # Converteer market_area naar Entso-e Area code
        if market_area not in MARKET_AREA_TO_ENTSOE:
            return pd.DataFrame(columns=["q", "price"])
        
        area_code = MARKET_AREA_TO_ENTSOE[market_area]
        
        # Stel start en end timestamps in (begin van dag tot einde van dag)
        # Gebruik Europe/Brussels timezone voor consistentie met Entso-e
        start = pd.Timestamp(delivery_date, tz="Europe/Brussels")
        end = start + pd.Timedelta(days=1)
        
        # Query de day-ahead prijzen
        prices = client.query_day_ahead_prices(area_code, start=start, end=end)
        
        if prices is None or len(prices) == 0:
            return pd.DataFrame(columns=["q", "price"])
        
        # Bepaal of het hourly of quarterly data is
        if len(prices) <= 24:
            # Hourly data - converteer naar kwartieren
            df_q = convert_hourly_to_quarters(prices)
        else:
            # Quarterly data - direct gebruiken
            df_q = convert_quarters_to_standardized(prices)
        
        return df_q
        
    except Exception as e:
        # Bij fouten, retourneer leeg dataframe
        return pd.DataFrame(columns=["q", "price"])


# ----------------- Streamlit dashboard -----------------

st.set_page_config(
    page_title="Day-Ahead Prijzen Dashboard (Entso-e) - Alle landen",
    layout="wide",
)

st.title("Day-Ahead Prijzen Dashboard – Alle beschikbare landen")
st.caption(
    "Bron: Entso-e Transparency Platform via officiële API. "
    "Day-ahead prijzen worden opgehaald voor alle marktgebieden. "
    "Als uurprijzen beschikbaar zijn, worden deze naar 96 kwartieren opgeschaald (forward fill)."
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
        "Morgen werkt zodra de day-ahead prijzen gepubliceerd zijn "
        "(rond 12:45-13:15 via Entso-e)."
    ),
)

with st.spinner(f"Prijzen ophalen voor leveringsdag {selected_date}..."):
    all_frames: list[pd.DataFrame] = []
    skipped_markets: list[str] = []

    for ma in ENTSOE_MARKET_AREAS:
        df_ma = get_day_ahead_prices(selected_date, ma)
        if df_ma is None or df_ma.empty:
            skipped_markets.append(ma)
            continue
        df_ma = df_ma.copy()
        df_ma["market_area"] = ma
        all_frames.append(df_ma)

if not all_frames:
    st.error(
        "Geen Day-Ahead prijzen gevonden voor deze datum in de gekozen marktgebieden. "
        "Mogelijk nog niet gepubliceerd of niet beschikbaar via Entso-e API."
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
    file_name=f"DayAhead_Entsoe_quarters_all_markets_{selected_date}.csv",
    mime="text/csv",
)

if skipped_markets:
    st.caption(
        "Voor de volgende marktgebieden kon geen Day-Ahead data worden gevonden "
        "(niet beschikbaar via Entso-e API): "
        + ", ".join(skipped_markets)
    )

st.caption(
    "Let op: dit dashboard gebruikt de Entso-e Transparency Platform API. "
    "Data is afhankelijk van beschikbaarheid op het platform."
)
