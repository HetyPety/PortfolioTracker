import traceback
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

selected_tickers = st.multiselect(
    "Filter News by Ticker",
    options=active_tickers,
    default=active_tickers,
)

with st.spinner("Scraping live official press releases..."):
    news_df = engine.fetch_portfolio_news(selected_tickers, ir_url_map)

st.divider()

if not news_df.empty:
    st.markdown(f"### 📋 Latest Official Releases ({len(news_df)} found)")

    for idx, row in news_df.iterrows():
        ticker = row["Ticker"]
        title = row["Title"]
        url = row["Url"]
        domain = row["Domain"]

        with st.container():
            col_t, col_c = st.columns([1, 6])
            with col_t:
                st.markdown(f"### 🟢 `{ticker}`")
                st.caption(f"🌐 {domain}")
            with col_c:
                st.markdown(f"#### [{title}]({url})")
                st.markdown(f"🔗 [Open Official Article on {domain}]({url})")
            st.divider()
else:
    st.info(
        "No official press releases could be loaded for the selected tickers. "
        "Make sure the `IR_Page_Url` column is populated in the `Ticker_Mapping` tab of your Google Sheet."
    )
