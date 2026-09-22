import traceback
import pandas as pd
import streamlit as st
from engine import (
    calculate_weighted_positions,
    enrich_with_live_prices,
    load_and_sync_portfolio,
)

st.set_page_config(
    page_title="Holdings & Transactions", page_icon="🔍", layout="wide"
)

st.title("🔍 Active Holdings & Transaction Logs")

DEFAULT_SHEET_URL = "https://docs.google.com/spreadsheets/d/1npPdS9fw30_pXxLpmjRYySjLm-KY-OgxSkE0tICxV7E/edit?gid=813187064#gid=813187064"

SHEET_NAME_OR_URL = st.sidebar.text_input(
    "Google Sheet Name or URL",
    value=DEFAULT_SHEET_URL,
)
currency_mode = st.sidebar.radio("Display Currency", ["HUF", "EUR"])

if st.sidebar.button("Refresh Portfolio Data"):
    st.cache_data.clear()
    st.cache_resource.clear()
    st.rerun()

if not SHEET_NAME_OR_URL:
    st.warning("Please enter your Google Sheet Name or URL in the sidebar.")
    st.stop()

with st.spinner("Fetching portfolio data and live market prices..."):
    try:
        df_tx, ticker_map = load_and_sync_portfolio(
            SHEET_NAME_OR_URL, force_resync=True
        )
        active_df, realized_df = calculate_weighted_positions(df_tx, ticker_map)
        enriched_df, eur_huf_rate = enrich_with_live_prices(active_df)
    except Exception as e:
        st.error(f"Error loading portfolio: {e}")
        st.code(traceback.format_exc(), language="text")
        st.stop()

# Filters
col_f1, col_f2 = st.columns(2)
brokers = ["All Brokers"] + sorted(df_tx["Broker"].unique().tolist()) if not df_tx.empty else ["All Brokers"]
selected_broker = col_f1.selectbox("Filter Broker", brokers)

accounts = (
    ["All Accounts"] + sorted(df_tx["Account"].unique().tolist())
    if not df_tx.empty and "Account" in df_tx.columns
    else ["All Accounts"]
)
selected_account = col_f2.selectbox("Filter Account", accounts)

display_df = enriched_df.copy() if not enriched_df.empty else pd.DataFrame()
if not display_df.empty:
    if selected_broker != "All Brokers":
        display_df = display_df[display_df["Broker"] == selected_broker]
    if selected_account != "All Accounts":
        display_df = display_df[display_df["Account"] == selected_account]

tab_holdings, tab_closed, tab_divs, tab_txs = st.tabs([
    "Active Holdings",
    "Realized Performance",
    "Dividends & Taxes",
    "All Transactions",
])

with tab_holdings:
    if not display_df.empty:
        cols = [
            "Broker",
            "Account",
            "Ticker",
            "Shares",
            "Live Price",
            "Currency",
            "PnL %",
            "Div Yield %",
            "Next Earnings",
        ]
        cols += (
            ["Market Value EUR", "PnL EUR"]
            if currency_mode == "EUR"
            else ["Market Value HUF", "PnL HUF"]
        )
        st.dataframe(display_df[cols], use_container_width=True, hide_index=True)
    else:
        st.info("No active holdings found.")

with tab_closed:
    if not realized_df.empty:
        r_df = realized_df.copy()
        if selected_broker != "All Brokers":
            r_df = r_df[r_df["Broker"] == selected_broker]
        if selected_account != "All Accounts":
            r_df = r_df[r_df["Account"] == selected_account]
        st.dataframe(r_df, use_container_width=True, hide_index=True)
    else:
        st.info("No closed positions found.")

with tab_divs:
    if not df_tx.empty:
        type_col = next((c for c in df_tx.columns if c.lower() in ["transaction type", "type", "buy/sell", "tipus"]), None)
        if type_col:
            div_df = df_tx[df_tx[type_col].astype(str).str.strip().str.title().isin(["Dividend", "Foreign Tax Withholding"])]
            if selected_broker != "All Brokers":
                div_df = div_df[div_df["Broker"] == selected_broker]
            if selected_account != "All Accounts":
                acc_col = next((c for c in div_df.columns if c.lower() == "account"), None)
                if acc_col:
                    div_df = div_df[div_df[acc_col].astype(str).str.strip() == selected_account]
            if not div_df.empty:
                st.dataframe(div_df, use_container_width=True, hide_index=True)
            else:
                st.info("No dividends or tax withholding records found.")
        else:
            st.info("Transaction type column not found.")
    else:
        st.info("No transaction history available.")

with tab_txs:
    if not df_tx.empty:
        tx_display = df_tx.copy()
        if selected_broker != "All Brokers":
            tx_display = tx_display[tx_display["Broker"] == selected_broker]
        if selected_account != "All Accounts":
            tx_display = tx_display[tx_display["Account"] == selected_account]
        st.dataframe(tx_display, use_container_width=True, hide_index=True)
    else:
        st.info("No transaction history recorded.")
