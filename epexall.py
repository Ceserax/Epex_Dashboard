from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import altair as alt
from entsoe import EntsoePandasClient

# ----------------- Config -----------------

# Entso-e API key
ENTSOE_API_KEY = "88f62dd9-0372-434c-8bc8-52b3c36a127f"

# Day-Ahead marktgebieden (bidding zones)
DAYAHEAD_MARKET_AREAS = [
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

# Mapping van UI namen naar Entso-e gebied codes
AREA_CODE_MAPPING = {
    "NL": "NL",
    "BE": "BE",
    "DE-LU": "DE_LU",
    "AT": "AT",
    "FR": "FR",
    "CH": "CH",
    "GB": "GB",
    "PL": "PL",
    "DK1": "DK_1",
    "DK2": "DK_2",
    "FI": "FI",
    "NO1": "NO_1",
    "NO2": "NO_2",
    "NO3": "NO_3",
    "NO4": "NO_4",
    "NO5": "NO_5",
    "SE1": "SE_1",
    "SE2": "SE_2",
    "SE3": "SE_3",
    "SE4": "SE_4",
}


# ----------------- Entso-e API helpers -----------------

def convert_hourly_to_quarter(prices_series: pd.Series) -> pd.DataFrame:
    """
    Zet uurlijkse prijzen (24 waarden) om naar kwartieren (96 waarden).
    Elke uurprijs wordt herhaald voor 4 kwartieren.
    
    Parameters
    ----------
    prices_series : pd.Series
        Pandas Series met uurlijkse prijzen (index is timestamp)
    
    Returns
    -------
    pd.DataFrame
        DataFrame met kolommen ['q', 'price'], q = 1..96
    """
    rows = []
    for hour_idx, (timestamp, price) in enumerate(prices_series.items()):
        if pd.isna(price):
            continue
        # q's voor dit uur: 1..4, 5..8, ...
        base_q = hour_idx * 4
        for offset in range(4):
            q = base_q + offset + 1
            if 1 <= q <= 96:
                rows.append({"q": q, "price": price})
    
    return pd.DataFrame(rows)


@st.cache_data(show_spinner=False)
def get_dayahead_quarter_prices(delivery_date: date, market_area: str) -> pd.DataFrame:
    """
    Haalt Day-Ahead prijzen op via Entso-e API als kwartierprofiel (q=1..96) voor
    een gegeven leveringsdatum en marktgebied.
    
    De Entso-e API retourneert uurlijkse prijzen, die worden omgezet naar 
    96 kwartieren door elke uurprijs 4x te herhalen.
    
    Parameters
    ----------
    delivery_date : date
        De leveringsdatum waarvoor Day-Ahead prijzen worden opgehaald
    market_area : str
        Marktgebied zoals gedefinieerd in DAYAHEAD_MARKET_AREAS (bijv. "NL", "DE-LU")
    
    Returns
    -------
    pd.DataFrame
        DataFrame met kolommen ['q', 'price'], q = 1..96
        Leeg DataFrame als er geen data beschikbaar is
    """
    try:
        # Map UI naam naar Entso-e gebied code
        entsoe_area = AREA_CODE_MAPPING.get(market_area)
        if not entsoe_area:
            return pd.DataFrame(columns=["q", "price"])
        
        # Initialiseer Entso-e client
        client = EntsoePandasClient(api_key=ENTSOE_API_KEY)
        
        # Timestamps voor de query (hele dag in UTC)
        start = pd.Timestamp(delivery_date, tz="UTC")
        end = start + pd.Timedelta(days=1)
        
        # Query Day-Ahead prijzen
        prices = client.query_day_ahead_prices(entsoe_area, start=start, end=end)
        
        # Converteer naar lokale tijd en extracteer alleen de prijzen
        # De Entso-e API geeft prijzen in EUR/MWh
        if isinstance(prices, pd.Series):
            # Zorg ervoor dat we precies 24 uurwaarden hebben
            if len(prices) == 24:
                # Direct naar kwartieren converteren
                df_q = convert_hourly_to_quarter(prices)
                return df_q
            else:
                # Als we meer of minder dan 24 waarden hebben, probeer te resamples
                # Dit kan gebeuren bij DST overgangen
                prices_hourly = prices.resample('H').mean()
                df_q = convert_hourly_to_quarter(prices_hourly)
                return df_q
        
        return pd.DataFrame(columns=["q", "price"])
        
    except Exception as e:
        # Bij fouten (bijv. geen data beschikbaar), retourneer leeg DataFrame
        # Dit voorkomt dat de hele app crasht voor één marktgebied
        return pd.DataFrame(columns=["q", "price"])


# ----------------- Streamlit dashboard -----------------

st.set_page_config(
    page_title="Day-Ahead Prijzen Dashboard (Entso-e) - Alle landen",
    layout="wide",
)

st.title("Day-Ahead Prijzen Dashboard – Alle beschikbare landen")
st.caption(
    "Bron: Entso-e Transparency Platform via officiële API. "
    "De API retourneert uurlijkse Day-Ahead prijzen die worden omgezet naar "
    "96 kwartieren (elke uurprijs wordt 4x herhaald voor elk kwartier)."
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
        "Morgen werkt zodra de Day-Ahead prijzen gepubliceerd zijn "
        "via de Entso-e API (rond 12:45-13:15)."
    ),
)

with st.spinner(f"Prijzen ophalen voor leveringsdag {selected_date}..."):
    all_frames: list[pd.DataFrame] = []
    skipped_markets: list[str] = []

    for ma in DAYAHEAD_MARKET_AREAS:
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
        "Mogelijk nog niet gepubliceerd via de Entso-e API."
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
- De Entso-e API levert uurprijzen die **naar 96 kwartieren worden verdeeld** (zelfde prijs in de 4 kwartieren van dat uur).
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
        "(mogelijk nog niet gepubliceerd of niet beschikbaar via Entso-e API): "
        + ", ".join(skipped_markets)
    )

st.caption(
    "Let op: dit dashboard gebruikt de Entso-e Transparency Platform API. "
    "Data is afhankelijk van beschikbaarheid en publicatie door de Transmission System Operators (TSO's)."
)
