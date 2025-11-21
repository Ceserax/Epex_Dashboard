import time
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import altair as alt
from entsoe import EntsoePandasClient
import yfinance as yf

# ----------------- Config -----------------

API_KEY = '88f62dd9-0372-434c-8bc8-52b3c36a127f'

# Day-Ahead market areas (Entso-E naming convention mapped below)
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

# Mapping from dashboard names to Entso-E specific codes
ENTSOE_AREA_MAPPING = {
    "DE-LU": "DE_LU",
    "DK1": "DK_1", "DK2": "DK_2",
    "NO1": "NO_1", "NO2": "NO_2", "NO3": "NO_3", "NO4": "NO_4", "NO5": "NO_5",
    "SE1": "SE_1", "SE2": "SE_2", "SE3": "SE_3", "SE4": "SE_4",
}

# ----------------- Entso-E helpers -----------------

@st.cache_data(show_spinner=False)
def get_dayahead_quarter_prices(delivery_date: date, market_area: str) -> pd.DataFrame:
    """
    Fetches Day-Ahead prices via Entso-E API and converts them to
    a quarter-hourly profile (q=1..96).
    """
    client = EntsoePandasClient(api_key=API_KEY)
    
    # Map to Entso-E code
    country_code = ENTSOE_AREA_MAPPING.get(market_area, market_area)
    
    # Determine timezone (dashboard runs on NL time)
    tz = ZoneInfo("Europe/Amsterdam")
    
    # Start and end for the query
    # We request the full day in the local timezone
    start = pd.Timestamp(delivery_date, tz=tz)
    end = start + pd.Timedelta(days=1)
    
    try:
        # Query Day Ahead Prices
        series = client.query_day_ahead_prices(country_code, start=start, end=end)
    except Exception:
        # Error fetching (e.g. data not available yet, 404, or invalid zone)
        return pd.DataFrame(columns=["q", "price"])

    if series.empty:
        return pd.DataFrame(columns=["q", "price"])

    # Convert to DataFrame
    df = series.to_frame(name="price")
    
    # Ensure 15-min resolution (resample + forward fill for hourly prices)
    full_idx = pd.date_range(start=start, end=end, freq='15min', inclusive='left')
    
    # Reindex to 15 min
    df = df.reindex(full_idx, method='ffill')
    
    # Add q column (1..96)
    df.index = full_idx
    df['q'] = (df.index.hour * 4) + (df.index.minute // 15) + 1
    
    # Cleanup
    df = df.reset_index(drop=True)
    df = df[df['q'] <= 96] # Safety check
    
    return df[["q", "price"]]


@st.cache_data(show_spinner=False)
def get_gas_prices(days: int = 30) -> pd.DataFrame:
    """
    Fetches Dutch TTF Gas prices (TTF=F) for the last 'days' days using yfinance.
    Returns a DataFrame with Date and Close price columns.
    """
    try:
        # Fetch TTF=F (Dutch TTF Natural Gas Calendar Month Futures)
        ticker = yf.Ticker("TTF=F")
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        # Get historical data
        hist = ticker.history(start=start_date, end=end_date)
        
        if hist.empty:
            return pd.DataFrame(columns=["Date", "Price"])
        
        # Prepare dataframe
        df = hist[['Close']].copy()
        df = df.reset_index()
        df.columns = ['Date', 'Price']
        df['Date'] = pd.to_datetime(df['Date']).dt.date
        
        return df
    except Exception:
        # Error fetching data
        return pd.DataFrame(columns=["Date", "Price"])


# ----------------- Streamlit dashboard -----------------

st.set_page_config(
    page_title="Entso-e Day-Ahead 15-min/60-min Dashboard",
    layout="wide",
)

st.title("Entso-e Day-Ahead Dashboard – All Available Countries")
st.caption(
    "Source: Entso-E Transparency Platform. "
    "Day-Ahead prices are fetched per market area. Hourly prices are automatically "
    "converted to 4 identical quarter prices."
)
tz = ZoneInfo("Europe/Amsterdam")
today = date.today()
tomorrow = today + timedelta(days=1)

# Date selection
selected_date = st.date_input(
    "Choose delivery date",
    value=tomorrow if datetime.now().hour >= 13 else today,
    min_value=today - timedelta(days=7),
    max_value=tomorrow,
    help=(
        "Tomorrow works as soon as the Day-ahead prices are published "
        "(usually around 13:00)."
    ),
)

with st.spinner(f"Fetching prices for delivery day {selected_date}..."):
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
        "No Day-Ahead prices found for this date in the selected market areas. "
        "Possibly not yet published on Entso-E."
    )
    st.stop()

df_all = pd.concat(all_frames, ignore_index=True)
df_all["market_area"] = df_all["market_area"].astype("string")
df_all["q"] = df_all["q"].astype(int)

available_markets = sorted(df_all["market_area"].unique())

st.subheader("Market Area Selection")
selected_markets = st.multiselect(
    "Market areas for comparison (click in legend to toggle lines):",
    options=available_markets,
    default=available_markets,
)

if not selected_markets:
    st.warning("Select at least one market area.")
    st.stop()

plot_df = df_all[df_all["market_area"].isin(selected_markets)].copy()

# ---- Key Figures ----
st.subheader("Key Figures per Market Area (Day-Ahead, as quarters)")

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
            "Market Area": ma,
            "Baseload (avg) [€/MWh]": round(baseload, 2),
            "Peakload 09–20 [€/MWh]": round(peakload, 2) if not pd.isna(peakload) else None,
            "Min [€/MWh]": round(min_price, 2),
            "Max [€/MWh]": round(max_price, 2),
            "Std. dev. [€/MWh]": round(std_dev, 2),
        }
    )

kpi_df = pd.DataFrame(kpi_rows)
st.dataframe(kpi_df, use_container_width=True)

# ---- Chart ----
st.subheader("Price Profile per Quarter – Comparison between Market Areas")

# Data for vertical lines
quarter_lines_data = pd.DataFrame({"q": list(range(1, 97))})
hour_lines_data = pd.DataFrame(
    {"q_hour": [h * 4 for h in range(1, 25) if h * 4 <= 96]}
)

# Light grey lines for every quarter
quarter_lines = (
    alt.Chart(quarter_lines_data)
    .mark_rule(strokeWidth=0.5, color="#eeeeee")
    .encode(x="q:Q")
)

# Darker lines for every hour
hour_lines = (
    alt.Chart(hour_lines_data)
    .mark_rule(strokeWidth=1, color="#bbbbbb")
    .encode(x="q_hour:Q")
)

# Interactive selection via legend
selection = alt.selection_point(fields=["market_area"], bind="legend", toggle="true")

base = alt.Chart(plot_df).encode(
    x=alt.X(
        "q:Q",
        title="Quarter of the day (1–96) – light lines = quarters, dark lines = hours",
        scale=alt.Scale(domain=(1, 96)),
        axis=alt.Axis(
            values=[h * 4 for h in range(1, 25) if h * 4 <= 96],
            labelExpr="(datum.value / 4) + 'h'",
            labelAngle=0,
        ),
    ),
    y=alt.Y("price:Q", title="Price (€/MWh)"),
    color=alt.Color(
        "market_area:N",
        title="Market Area",
        legend=alt.Legend(title="Market Area (click to filter)"),
    ),
    tooltip=[
        alt.Tooltip("market_area:N", title="Market"),
        alt.Tooltip("q:Q", title="Quarter"),
        alt.Tooltip("price:Q", title="Price (€/MWh)", format=".2f"),
    ],
)

lines = (
    base.mark_line(interpolate="monotone", strokeWidth=2)
    .encode(
        opacity=alt.condition(selection, alt.value(1.0), alt.value(0.2)),
    )
    .add_params(selection)
)

chart = (quarter_lines + hour_lines + lines).properties(
    width="container",
    height=450,
)

st.altair_chart(chart, width="stretch")

# ---- Explanation ----
st.markdown(
    """
**Legend / Explanation:**

- Each **color** represents a different *market area* (via Entso-E).  
- The **light vertical lines** indicate every 15-minute period (quarter).  
- The **dark vertical lines** mark the **hours** (1h, 2h, ..., 24h).  
- Click in the **legend** on a market area to filter.
"""
)

# ---- Download button ----
st.subheader("Download data")

csv_data = df_all.to_csv(index=False)
st.download_button(
    "Download all 15-minute equivalent prices (CSV) for selected date",
    data=csv_data,
    file_name=f"Entsoe_DayAhead_quarters_all_markets_{selected_date}.csv",
    mime="text/csv",
)

# ---- Dutch Gas Price Section ----
st.subheader("Dutch Gas Price (TTF) Development – Last 30 Days")
st.caption(
    "Source: Yahoo Finance (TTF=F). "
    "Shows the Dutch TTF Natural Gas Calendar Month Futures price trend."
)

with st.spinner("Fetching gas prices..."):
    gas_df = get_gas_prices(days=30)

if gas_df.empty:
    st.warning("No gas price data available for the selected period.")
else:
    # Convert Date to datetime for Altair
    gas_df['Date'] = pd.to_datetime(gas_df['Date'])
    
    # Create gas price chart
    gas_chart = alt.Chart(gas_df).mark_line(
        point=True,
        strokeWidth=2,
        color='#FF6B6B'
    ).encode(
        x=alt.X('Date:T', title='Date', axis=alt.Axis(format='%b %d')),
        y=alt.Y('Price:Q', title='Price (€/MWh)'),
        tooltip=[
            alt.Tooltip('Date:T', title='Date', format='%Y-%m-%d'),
            alt.Tooltip('Price:Q', title='Price (€/MWh)', format='.2f')
        ]
    ).properties(
        width='container',
        height=350
    )
    
    st.altair_chart(gas_chart, use_container_width=True)
    
    # Show some basic statistics
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Average Price", f"€{gas_df['Price'].mean():.2f}/MWh")
    with col2:
        st.metric("Min Price", f"€{gas_df['Price'].min():.2f}/MWh")
    with col3:
        st.metric("Max Price", f"€{gas_df['Price'].max():.2f}/MWh")
    with col4:
        st.metric("Latest Price", f"€{gas_df['Price'].iloc[-1]:.2f}/MWh")

if skipped_markets:
    st.caption(
        "For the following market areas, no Day-Ahead table could be found "
        "(no data on Entso-E or API error): "
        + ", ".join(skipped_markets)
    )
