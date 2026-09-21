import traceback
import pandas as pd
import streamlit as st
from engine import (
    calculate_cash_and_nav,
    calculate_weighted_positions,
    enrich_with_live_prices,
    get_breakdown_summary,
    load_and_sync_portfolio,
)

st.cache_data.clear()
st.cache_resource.clear()

st.set_page_config(
    page_title="Portfolio Control Center", page_icon="📈", layout="wide"
)

st.title("📈 Portfolio Control Center")

SHEET_NAME_OR_URL = st.sidebar.text_input(
    "Google Sheet Name or URL",
    value="My Portfolio Holdings",
)
currency_mode = st.sidebar.radio("Display Currency", ["EUR", "HUF"])

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

# Sidebar Filters
brokers = ["All Brokers"] + sorted(df_tx["Broker"].unique().tolist()) if not df_tx.empty else ["All Brokers"]
selected_broker = st.sidebar.selectbox("Filter Broker", brokers)

accounts = (
    ["All Accounts"] + sorted(df_tx["Account"].unique().tolist())
    if not df_tx.empty and "Account" in df_tx.columns
    else ["All Accounts"]
)
selected_account = st.sidebar.selectbox("Filter Account", accounts)

nav_stats = calculate_cash_and_nav(
    df_tx, enriched_df, selected_broker, selected_account
)

# Render Top Portfolio Overview Metrics
if currency_mode == "EUR":
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(
        "Total Portfolio NAV", f"€{nav_stats['Total NAV HUF'] / eur_huf_rate:,.2f}"
    )
    c2.metric(
        "Uninvested Cash", f"€{nav_stats['Cash Balance HUF'] / eur_huf_rate:,.2f}"
    )
    c3.metric(
        "Total Net Return",
        f"€{nav_stats['Net Gain HUF'] / eur_huf_rate:,.2f}",
        f"{nav_stats['Net Return %']:+.2f}%",
    )
    c4.metric(
        "Annualized XIRR",
        f"{nav_stats['Annualized XIRR %']:+.2f}%",
        "Money-Weighted Rate"
    )
    c5.metric("Live EUR/HUF Rate", f"{eur_huf_rate:.2f} HUF")
else:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Portfolio NAV", f"{nav_stats['Total NAV HUF']:,.0f} HUF")
    c2.metric("Uninvested Cash", f"{nav_stats['Cash Balance HUF']:,.0f} HUF")
    c3.metric(
        "Total Net Return",
        f"{nav_stats['Net Gain HUF']:,.0f} HUF",
        f"{nav_stats['Net Return %']:+.2f}%",
    )
    c4.metric(
        "Annualized XIRR",
        f"{nav_stats['Annualized XIRR %']:+.2f}%",
        "Money-Weighted Rate"
    )
    c5.metric("Live EUR/HUF Rate", f"{eur_huf_rate:.2f} HUF")

st.divider()

# Broker / Account Summary Breakdown Section
st.subheader("📊 Broker & Account Breakdown")
df_breakdown = get_breakdown_summary(df_tx, enriched_df, eur_huf_rate)

if not df_breakdown.empty:
    st.dataframe(
        df_breakdown.style.format({
            "Deposits (HUF)": "{:,.0f}",
            "Cash Balance (HUF)": "{:,.0f}",
            "Invested Market Value (HUF)": "{:,.0f}",
            "Total NAV (HUF)": "{:,.0f}",
            "Net Return %": "{:+.2f}%",
            "Annualized XIRR %": "{:+.2f}%",
            "Total NAV (EUR)": "€{:,.2f}",
        }),
        use_container_width=True,
        hide_index=True,
    )

st.divider()

# Filter active holdings display
display_df = enriched_df.copy() if not enriched_df.empty else pd.DataFrame()
if not display_df.empty:
    if selected_broker != "All Brokers":
        display_df = display_df[display_df["Broker"] == selected_broker]
    if selected_account != "All Accounts":
        display_df = display_df[display_df["Account"] == selected_account]

# Tabbed Interface
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