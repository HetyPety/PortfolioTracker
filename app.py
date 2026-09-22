import traceback
import pandas as pd
import streamlit as st
import engine

st.cache_data.clear()
st.cache_resource.clear()

st.set_page_config(
    page_title="Executive Dashboard", page_icon="🌐", layout="wide"
)

st.title("🌐 Portfolio Executive Dashboard")

DEFAULT_SHEET_URL = "https://docs.google.com/spreadsheets/d/1npPdS9fw30_pXxLpmjRYySjLm-KY-OgxSkE0tICxV7E/edit?gid=813187064#gid=813187064"

SHEET_NAME_OR_URL = st.sidebar.text_input(
    "Google Sheet Name or URL",
    value=DEFAULT_SHEET_URL,
)
currency_mode = st.sidebar.radio("Display Currency", ["HUF", "EUR"], index=0)

if st.sidebar.button("Refresh Portfolio Data"):
    st.cache_data.clear()
    st.cache_resource.clear()
    st.rerun()

if not SHEET_NAME_OR_URL:
    st.warning("Please enter your Google Sheet Name or URL in the sidebar.")
    st.stop()

with st.spinner("Fetching portfolio data and live market prices..."):
    try:
        df_tx, ticker_map = engine.load_and_sync_portfolio(
            SHEET_NAME_OR_URL, force_resync=True
        )
        active_df, realized_df = engine.calculate_weighted_positions(df_tx, ticker_map)
        enriched_df, eur_huf_rate = engine.enrich_with_live_prices(active_df)
    except Exception as e:
        st.error(f"Error loading portfolio: {e}")
        st.code(traceback.format_exc(), language="text")
        st.stop()

# ==============================================================================
# 1. GRAND TOTAL OVERVIEW
# ==============================================================================
st.subheader("🌐 Grand Total Portfolio Overview")

grand_total_stats = engine.calculate_cash_and_nav(df_tx, enriched_df, "All Brokers", "All Accounts")

curr_sym = "€" if currency_mode == "EUR" else ""
curr_suffix = "" if currency_mode == "EUR" else " HUF"
fx_factor = eur_huf_rate if currency_mode == "EUR" else 1.0

gt1, gt2, gt3, gt4, gt5, gt6 = st.columns(6)

gt1.metric(
    "Total Portfolio NAV",
    f"{curr_sym}{grand_total_stats['Total NAV HUF'] / fx_factor:,.0f}{curr_suffix}"
    if currency_mode == "HUF"
    else f"{curr_sym}{grand_total_stats['Total NAV HUF'] / fx_factor:,.2f}"
)
gt2.metric(
    "Total Deposits",
    f"{curr_sym}{grand_total_stats['Deposits HUF'] / fx_factor:,.0f}{curr_suffix}"
    if currency_mode == "HUF"
    else f"{curr_sym}{grand_total_stats['Deposits HUF'] / fx_factor:,.2f}"
)
gt3.metric(
    "Total Cash Balance",
    f"{curr_sym}{grand_total_stats['Cash Balance HUF'] / fx_factor:,.0f}{curr_suffix}"
    if currency_mode == "HUF"
    else f"{curr_sym}{grand_total_stats['Cash Balance HUF'] / fx_factor:,.2f}"
)
gt4.metric(
    "Total Portfolio Return",
    f"{curr_sym}{grand_total_stats['Net Gain HUF'] / fx_factor:,.0f}{curr_suffix}"
    if currency_mode == "HUF"
    else f"{curr_sym}{grand_total_stats['Net Gain HUF'] / fx_factor:,.2f}",
    f"{grand_total_stats['Net Return %']:+.2f}%",
)
gt5.metric(
    "Total Portfolio XIRR",
    f"{grand_total_stats['Annualized XIRR %']:+.2f}%",
    "Money-Weighted Rate"
)
gt6.metric("Live EUR/HUF Rate", f"{eur_huf_rate:.2f} HUF")

st.divider()

# ==============================================================================
# 2. ACCOUNTS OVERVIEW (INDIVIDUAL CARDS & BREAKDOWN TABLE)
# ==============================================================================
st.subheader("🏛️ Account-Level Breakdown")

df_breakdown = engine.get_breakdown_summary(df_tx, enriched_df, eur_huf_rate)

if not df_breakdown.empty:
    for idx, row in df_breakdown.iterrows():
        broker_name = row["Broker"]
        account_name = row["Account"]
        
        account_stats = engine.calculate_cash_and_nav(
            df_tx, enriched_df, selected_broker=broker_name, selected_account=account_name
        )

        with st.container():
            st.markdown(f"#### 🏦 **{broker_name}** — *{account_name}*")
            ac1, ac2, ac3, ac4, ac5 = st.columns(5)

            ac1.metric(
                "Account NAV",
                f"{curr_sym}{account_stats['Total NAV HUF'] / fx_factor:,.0f}{curr_suffix}"
                if currency_mode == "HUF"
                else f"{curr_sym}{account_stats['Total NAV HUF'] / fx_factor:,.2f}"
            )
            ac2.metric(
                "Deposits",
                f"{curr_sym}{account_stats['Deposits HUF'] / fx_factor:,.0f}{curr_suffix}"
                if currency_mode == "HUF"
                else f"{curr_sym}{account_stats['Deposits HUF'] / fx_factor:,.2f}"
            )
            ac3.metric(
                "Uninvested Cash",
                f"{curr_sym}{account_stats['Cash Balance HUF'] / fx_factor:,.0f}{curr_suffix}"
                if currency_mode == "HUF"
                else f"{curr_sym}{account_stats['Cash Balance HUF'] / fx_factor:,.2f}"
            )
            ac4.metric(
                "Account Return",
                f"{curr_sym}{account_stats['Net Gain HUF'] / fx_factor:,.0f}{curr_suffix}"
                if currency_mode == "HUF"
                else f"{curr_sym}{account_stats['Net Gain HUF'] / fx_factor:,.2f}",
                f"{account_stats['Net Return %']:+.2f}%",
            )
            ac5.metric(
                "Account XIRR",
                f"{account_stats['Annualized XIRR %']:+.2f}%"
            )
            st.markdown("---")

    with st.expander("📋 View Summary Table Across All Accounts"):
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
