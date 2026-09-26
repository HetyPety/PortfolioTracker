import traceback
from datetime import datetime
import pandas as pd
import streamlit as st
import engine

st.set_page_config(
    page_title="Company Newsroom", page_icon="📰", layout="wide"
)

st.title("📰 Company Newsroom & Official Releases")

DEFAULT_SHEET_URL = "https://docs.google.com/spreadsheets/d/1npPdS9fw30_pXxLpmjRYySjLm-KY-OgxSkE0tICxV7E/edit?gid=813187064#gid=813187064"

SHEET_NAME_OR_URL = st.sidebar.text_input(
    "Google Sheet Name or URL",
    value=DEFAULT_SHEET_URL,
)

if st.sidebar.button("Refresh News Feed"):
    st.cache_data.clear()
    st.cache_resource.clear()
    st.rerun()

if not SHEET_NAME_OR_URL:
    st.warning("Please enter your Google Sheet Name or URL in the sidebar.")
    st.stop()

with st.spinner("Fetching official press releases from company IR portals..."):
    try:
        df_tx, ticker_map, tax_map = engine.load_and_sync_portfolio(
            SHEET_NAME_OR_URL, force_resync=True
        )
        ir_url_map = engine.load_ir_url_map(SHEET_NAME_OR_URL)
        active_df, _ = engine.calculate_weighted_positions(df_tx, ticker_map)
        
        active_tickers = sorted(active_df["Ticker"].unique().tolist()) if not active_df.empty else []
    except Exception as e:
        st.error(f"Error loading portfolio tickers: {e}")
        st.code(traceback.format_exc(), language="text")
        st.stop()

if not active_tickers:
    st.info("No active portfolio holdings found.")
    st.stop()

col_f1, col_f2, col_f3 = st.columns([3, 2, 2])

with col_f1:
    selected_tickers = st.multiselect(
        "Filter Tickers",
        options=active_tickers,
        default=active_tickers,
    )

with col_f2:
    time_filter = st.selectbox(
        "Date Filter",
        ["All Time", "Last 30 Days", "Last 90 Days", "Last 365 Days"],
        index=0,
    )

with col_f3:
    sort_order = st.selectbox(
        "Sort Order",
        ["Newest First", "Oldest First"],
        index=0,
    )

with st.spinner("Scraping live official press releases..."):
    news_df = engine.fetch_portfolio_news(selected_tickers, ir_url_map)

st.divider()

if not news_df.empty:
    filtered_df = news_df.copy()

    if time_filter == "Last 30 Days":
        cutoff = datetime.now() - pd.Timedelta(days=30)
        filtered_df = filtered_df[filtered_df["Date_Obj"] >= cutoff]
    elif time_filter == "Last 90 Days":
        cutoff = datetime.now() - pd.Timedelta(days=90)
        filtered_df = filtered_df[filtered_df["Date_Obj"] >= cutoff]
    elif time_filter == "Last 365 Days":
        cutoff = datetime.now() - pd.Timedelta(days=365)
        filtered_df = filtered_df[filtered_df["Date_Obj"] >= cutoff]

    if sort_order == "Oldest First":
        filtered_df = filtered_df.sort_values(by="Date_Obj", ascending=True)
    else:
        filtered_df = filtered_df.sort_values(by="Date_Obj", ascending=False)

    st.markdown(f"### 📋 Official Press Releases ({len(filtered_df)} shown)")

    for idx, row in filtered_df.iterrows():
        ticker = row["Ticker"]
        title = row["Title"]
        url = row["Url"]
        domain = row["Domain"]
        date_str = row["Date_Str"]
        snippet = row.get("Snippet", "")

        date_badge = f"📅 **{date_str}**" if date_str != "Date N/A" else "📅 *Date N/A*"

        with st.container():
            col_left, col_right = st.columns([2, 8])
            with col_left:
                st.markdown(f"### 🟢 `{ticker}`")
                st.markdown(date_badge)
                st.caption(f"🌐 {domain}")
            with col_right:
                st.markdown(f"#### [{title}]({url})")
                if snippet:
                    st.markdown(f"> *{snippet}*")
                st.markdown(f"🔗 [Open Official Article on {domain}]({url})")
            st.divider()
else:
    st.info(
        "No official press releases found for the selected options."
    )
