from datetime import datetime, date, timedelta
import pandas as pd
import streamlit as st
import altair as alt
from zoneinfo import ZoneInfo
from entsoe import EntsoePandasClient
from entsoe.mappings import Area

# ----------------- Config -----------------

# ENTSO-E API key
ENTSOE_API_KEY = "88f62dd9-0372-434c-8bc8-52b3c36a127f"

# Day-Ahead marktgebieden (bidding zones) met mapping naar ENTSO-E codes
DAYAHEAD_MARKET_AREAS = {
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


# ----------------- ENTSO-E API helpers -----------------

# Initialiseer de ENTSO-E client
client = EntsoePandasClient(api_key=ENTSOE_API_KEY)


def convert_series_to_quarters(series: pd.Series) -> pd.DataFrame:
    """
    Zet een pandas Series met tijdindex om naar kwartieren (q=1..96).
    Ondersteunt zowel uurprijzen als kwartierprijzen.
    """
    rows = []
    
    # Bepaal de frequentie van de data
    if len(series) == 0:
        return pd.DataFrame(columns=["q", "price"])
    
    # Sorteer op index
    series = series.sort_index()
    
    for timestamp, price in series.items():
        if pd.isna(price):
            continue
            
        # Bereken kwartier nummer (1-96)
        hour = timestamp.hour
        minute = timestamp.minute
        q = hour * 4 + (minute // 15) + 1
        
        # Als het uurprijzen zijn (op het hele uur), herhaal voor alle 4 kwartieren
        if minute == 0 and len(series) <= 24:
            # Uurprijzen - herhaal voor alle 4 kwartieren van dit uur
            for offset in range(4):
                q_current = hour * 4 + offset + 1
                if 1 <= q_current <= 96:
                    rows.append({"q": q_current, "price": price})
        else:
            # Kwartierprijzen of andere resolutie
            if 1 <= q <= 96:
                rows.append({"q": q, "price": price})
    
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["q", "price"])
    
    # Verwijder duplicaten en sorteer
    df = df.drop_duplicates(subset=["q"]).sort_values("q").reset_index(drop=True)
    return df[["q", "price"]]


@st.cache_data(show_spinner=False)
def get_dayahead_quarter_prices(delivery_date: date, market_area: str) -> pd.DataFrame:
    """
    Haalt Day-Ahead prijzen op via ENTSO-E API als kwartierprofiel (q=1..96) voor
    een gegeven leveringsdatum en marktgebied.
    """
    try:
        # Haal de area code op
        area_code = DAYAHEAD_MARKET_AREAS.get(market_area)
        if area_code is None:
            return pd.DataFrame(columns=["q", "price"])
        
        # Stel start en eind tijden in (hele dag)
        # ENTSO-E gebruikt UTC, maar we willen lokale tijd voor de bidding zone
        start = pd.Timestamp(delivery_date, tz='Europe/Brussels')
        end = start + pd.Timedelta(days=1)
        
        # Haal prijzen op via ENTSO-E API
        prices = client.query_day_ahead_prices(area_code, start=start, end=end)
        
        # Converteer naar kwartieren
        df_q = convert_series_to_quarters(prices)
        return df_q
        
    except Exception as e:
        # Bij fouten (API down, geen data, etc.) geef lege DataFrame terug
        return pd.DataFrame(columns=["q", "price"])


# ----------------- Streamlit dashboard -----------------

st.set_page_config(
    page_title="Day-Ahead Prices Dashboard (ENTSO-E)",
    layout="wide",
)

st.title("Day-Ahead Prices Dashboard – Alle beschikbare landen")
st.caption(
    "Bron: ENTSO-E Transparency Platform via entsoe-py API. "
    "Prijzen worden opgehaald voor de geselecteerde leveringsdatum en "
    "weergegeven als 96 kwartieren per dag."
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
        "(rond 12:45-13:15)."
    ),
)

with st.spinner(f"Prijzen ophalen voor leveringsdag {selected_date}..."):
    all_frames: list[pd.DataFrame] = []
    skipped_markets: list[str] = []

    for ma in DAYAHEAD_MARKET_AREAS.keys():
        df_ma = get_dayahead_quarter_prices(selected_date, ma)
        if df_ma is None or df_ma.empty:
            skipped_markets.append(ma)
            continue
        df_ma = df_ma.copy()
        df_ma["market_area"] = ma
        all_frames.append(df_ma)

if not all_frames:
    st.error(
        "Geen Day-Ahead prijzen gevonden voor deze datum in de gekozen marktgebieden. "
        "Mogelijk nog niet gepubliceerd of niet beschikbaar via ENTSO-E API."
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

- Elke **kleur** staat voor een ander *marktgebied*.  
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
    file_name=f"DayAhead_quarters_all_markets_{selected_date}.csv",
    mime="text/csv",
)

if skipped_markets:
    st.caption(
        "Voor de volgende marktgebieden kon geen Day-Ahead data worden gevonden "
        "(geen data beschikbaar via ENTSO-E API): "
        + ", ".join(skipped_markets)
    )

st.caption(
    "Let op: dit dashboard gebruikt de ENTSO-E Transparency Platform API. "
    "Voor sommige marktgebieden is de data mogelijk niet beschikbaar of vertraagd."
)
