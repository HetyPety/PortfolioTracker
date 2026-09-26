import os
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse
import concurrent.futures
import gspread
import pandas as pd
import requests
from bs4 import BeautifulSoup
import yfinance as yf
import urllib3
import streamlit as st

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SUPPORTED_CURRENCIES = {
    "HUF", "EUR", "USD", "GBP", "CHF", "DKK", "NOK", "SEK",
    "PLN", "CZK", "RON", "CAD", "AUD", "JPY", "SGD", "GBPX",
    "GBX", "GBp", "ILS",
}

COUNTRY_NAME_TO_CODE = {
    "UNITED STATES": "US", "UNITED KINGDOM": "GB", "GERMANY": "DE",
    "AUSTRIA": "AT", "FINLAND": "FI", "SPAIN": "ES", "SWITZERLAND": "CH",
    "ITALY": "IT", "FRANCE": "FR", "DENMARK": "DK", "NETHERLANDS": "NL",
    "NEW ZEALAND": "NZ", "PORTUGAL": "PT", "SWEDEN": "SE", "NORWAY": "NO",
    "POLAND": "PL", "CZECHIA": "CZ", "CZECH REPUBLIC": "CZ",
    "AUSTRALIA": "AU", "CANADA": "CA", "JAPAN": "JP", "BELGIUM": "BE",
    "IRELAND": "IE", "LUXEMBOURG": "LU", "HUNGARY": "HU",
}

SUFFIX_TO_CODE = {
    ".VI": "AT", ".DE": "DE", ".F": "DE", ".HE": "FI", ".MC": "ES",
    ".SW": "CH", ".MI": "IT", ".PA": "FR", ".CO": "DK", ".AS": "NL",
    ".NZ": "NZ", ".LS": "PT", ".ST": "SE", ".OL": "NO", ".L": "GB",
}


def clean_float(val, default: float = 0.0) -> float:
    if val is None or pd.isna(val):
        return default
    if isinstance(val, (int, float)):
        return float(val)

    s = str(val).strip().replace("\xa0", "").replace(" ", "")
    if not s or s.lower() in ["nan", "none", "null", ""]:
        return default

    s = re.sub(r"[^\d.,\-+]", "", s)
    if not s:
        return default

    if "," in s and "." in s:
        if s.rfind(",") < s.rfind("."):
            s = s.replace(",", "")
        else:
            s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) in [1, 2]:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")

    try:
        return float(s)
    except ValueError:
        return default


def parse_date_string(date_val) -> datetime:
    if isinstance(date_val, (datetime, pd.Timestamp)):
        return datetime(date_val.year, date_val.month, date_val.day)

    s = str(date_val).strip()
    if " " in s and not re.search(r"\d{4}\.\s+\d{2}", s):
        s = s.split(" ")[0].strip()

    s = re.sub(r"[\.\/\s]+", "-", s).strip("-")
    parts = s.split("-")
    if len(parts) == 3:
        try:
            p1, p2, p3 = int(parts[0]), int(parts[1]), int(parts[2])
            if p1 > 1000:
                return datetime(p1, p2, p3)
            elif p3 > 1000:
                return datetime(p3, p2, p1)
        except ValueError:
            pass

    return pd.to_datetime(date_val, dayfirst=True).to_pydatetime()


def validate_and_parse_currency(currency_str: str):
    raw_curr = (
        str(currency_str)
        .replace("\xa0", "")
        .replace("\t", "")
        .replace("\n", "")
        .strip()
        .upper()
    )
    if not raw_curr:
        return "USD", False

    if raw_curr == "HUF":
        return "HUF", False
    elif raw_curr in ["GBPX", "GBX", "GBP"]:
        return ("GBP", False) if raw_curr == "GBP" else ("GBP", True)
    elif raw_curr in SUPPORTED_CURRENCIES:
        return raw_curr, False
    else:
        return raw_curr, False


def find_col_name(df_columns, possible_names, fallback_idx: int = -1) -> str:
    col_map = {str(c).strip().lower(): str(c) for c in df_columns}
    for name in possible_names:
        clean_name = str(name).strip().lower()
        if clean_name in col_map:
            return col_map[clean_name]
    if 0 <= fallback_idx < len(df_columns):
        return str(df_columns[fallback_idx])
    return ""


def detect_country_code(ticker: str, ticker_info=None) -> str:
    if ticker_info and isinstance(ticker_info, dict):
        country_name = str(ticker_info.get("country", "")).strip().upper()
        if country_name in COUNTRY_NAME_TO_CODE:
            return COUNTRY_NAME_TO_CODE[country_name]

    for suffix, code in SUFFIX_TO_CODE.items():
        if ticker.endswith(suffix):
            return code

    return "US"


def calculate_xirr(cash_flows, dates, estimate: float = 0.1) -> float:
    if not cash_flows or len(cash_flows) < 2 or sum(1 for c in cash_flows if c < 0) == 0:
        return 0.0

    d0 = min(dates)

    def npv(rate):
        if rate <= -0.9999:
            return float("inf")
        return sum([cf / ((1.0 + rate) ** ((d - d0).days / 365.0)) for cf, d in zip(cash_flows, dates)])

    def npv_derivative(rate):
        if rate <= -0.9999:
            return float("inf")
        return sum([-(((d - d0).days / 365.0)) * cf / ((1.0 + rate) ** (((d - d0).days / 365.0) + 1.0)) for cf, d in zip(cash_flows, dates)])

    r = estimate
    for _ in range(100):
        f_val = npv(r)
        if abs(f_val) < 1e-3:
            return r * 100.0
        f_prime = npv_derivative(r)
        if f_prime == 0:
            break
        r_next = r - f_val / f_prime
        if abs(r_next - r) < 1e-5:
            return r_next * 100.0
        r = r_next

    return 0.0


def get_live_fx_rate_to_huf(currency_code: str, is_pence: bool, eur_huf_rate: float) -> float:
    if currency_code == "HUF":
        return 1.0
    if currency_code == "EUR":
        return eur_huf_rate / 100.0 if is_pence else eur_huf_rate

    try:
        pair = f"{currency_code}HUF=X"
        t = yf.Ticker(pair)
        rate = float(t.fast_info.get("lastPrice") or 0.0)
        if rate > 0:
            return rate / 100.0 if is_pence else rate
    except Exception:
        pass

    try:
        usd_huf = float(yf.Ticker("USDHUF=X").fast_info.get("lastPrice") or 0.0)
        usd_curr = float(yf.Ticker(f"USD{currency_code}=X").fast_info.get("lastPrice") or 0.0)
        if usd_huf > 0 and usd_curr > 0:
            rate = usd_huf / usd_curr
            return rate / 100.0 if is_pence else rate
    except Exception:
        pass

    try:
        eur_curr = float(yf.Ticker(f"EUR{currency_code}=X").fast_info.get("lastPrice") or 0.0)
        if eur_huf_rate > 0 and eur_curr > 0:
            rate = eur_huf_rate / eur_curr
            return rate / 100.0 if is_pence else rate
    except Exception:
        pass

    return 1.0


def get_gspread_client(json_credentials_path: str = "credentials.json"):
    if hasattr(st, "secrets") and "gcp_service_account" in st.secrets:
        return gspread.service_account_from_dict(dict(st.secrets["gcp_service_account"]))
    
    if os.path.exists(json_credentials_path):
        return gspread.service_account(filename=json_credentials_path)
        
    raise FileNotFoundError("Google credentials not found in Streamlit Secrets or local credentials.json file!")


def load_and_sync_portfolio(
    sheet_name_or_url: str,
    json_credentials_path: str = "credentials.json",
    force_resync: bool = True,
):
    gc = get_gspread_client(json_credentials_path)
    sh = (
        gc.open_by_url(sheet_name_or_url)
        if sheet_name_or_url.startswith("http")
        else gc.open(sheet_name_or_url)
    )

    ticker_map = {}
    try:
        ws_map = sh.worksheet("Ticker_Mapping")
        map_rows = ws_map.get_all_records()
        for r in map_rows:
            ibkr_sym = str(r.get("IBKR_Symbol", "") or r.get("Symbol", "")).strip().upper()
            yf_sym = str(r.get("YFinance_Ticker", "") or r.get("YFinance", "") or r.get("Ticker", "")).strip()
            if ibkr_sym and yf_sym:
                ticker_map[ibkr_sym] = yf_sym
    except Exception:
        pass

    tax_map = {}
    try:
        ws_tax = sh.worksheet("Tax")
        tax_rows = ws_tax.get_all_records()
        for r in tax_rows:
            country_code = str(
                r.get("Country", "") or r.get("Orszag", "") or r.get("Country Code", "")
            ).strip().upper()
            tax_val_raw = (
                r.get("Withholding Tax", "")
                or r.get("Tax", "")
                or r.get("Tax %", "")
                or r.get("Withholding Tax %", "")
            )
            if country_code:
                tax_rate = clean_float(tax_val_raw)
                if tax_rate > 1.0:
                    tax_rate = tax_rate / 100.0
                tax_map[country_code] = tax_rate
                if country_code == "UK":
                    tax_map["GB"] = tax_rate
                elif country_code == "GB":
                    tax_map["UK"] = tax_rate
    except Exception:
        pass

    ws_tx = sh.worksheet("Transactions")
    all_tx_rows = ws_tx.get_all_values()

    df_tx = pd.DataFrame()
    if len(all_tx_rows) > 1:
        raw_headers = [str(h).strip() for h in all_tx_rows[0]]
        df_tx = pd.DataFrame(all_tx_rows[1:], columns=raw_headers).astype(object)

        broker_col = find_col_name(df_tx.columns, ["Broker", "Bróker", "Bank"])
        net_amt_col = find_col_name(df_tx.columns, ["Net Amount"])
        huf_amt_col = find_col_name(df_tx.columns, ["Amount HUF", "Net Amount HUF", "Net HUF", "Ertek HUF"])

        processed_huf_amounts = []
        processed_brokers = []

        for idx, row in df_tx.iterrows():
            net_amt = clean_float(row.get(net_amt_col, 0)) if net_amt_col else 0.0
            
            if huf_amt_col and str(row.get(huf_amt_col, "")).strip() != "":
                huf_val = clean_float(row.get(huf_amt_col, net_amt))
            else:
                huf_val = net_amt

            processed_huf_amounts.append(round(huf_val, 2))

            broker_val = str(row.get(broker_col, "")).strip() if broker_col else ""
            if not broker_val:
                broker_val = "IBKR"
            processed_brokers.append(broker_val)

        df_tx["_Net_Amount_HUF"] = processed_huf_amounts
        df_tx["Broker"] = processed_brokers

    return df_tx, ticker_map, tax_map


def load_ir_url_map(sheet_name_or_url: str, json_credentials_path: str = "credentials.json"):
    ir_map = {}
    try:
        gc = get_gspread_client(json_credentials_path)
        sh = (
            gc.open_by_url(sheet_name_or_url)
            if sheet_name_or_url.startswith("http")
            else gc.open(sheet_name_or_url)
        )
        ws_map = sh.worksheet("Ticker_Mapping")
        map_rows = ws_map.get_all_records()
        for r in map_rows:
            yf_sym = str(r.get("YFinance_Ticker", "") or r.get("YFinance", "") or r.get("Ticker", "")).strip()
            ibkr_sym = str(r.get("IBKR_Symbol", "") or r.get("Symbol", "")).strip().upper()
            ir_url = str(r.get("IR_Page_Url", "") or r.get("IR_Url", "") or r.get("IR_Page", "") or r.get("IR Page", "")).strip()
            
            if ir_url:
                if yf_sym:
                    ir_map[yf_sym] = ir_url
                if ibkr_sym:
                    ir_map[ibkr_sym] = ir_url
    except Exception:
        pass
    return ir_map


def calculate_weighted_positions(df_tx, ticker_map):
    if df_tx.empty:
        return pd.DataFrame(), pd.DataFrame()

    df_sorted = df_tx.copy()

    date_col = find_col_name(df_sorted.columns, ["Date", "Tx Date", "Datum"])
    broker_col = find_col_name(df_sorted.columns, ["Broker", "Bróker"])
    account_col = find_col_name(df_sorted.columns, ["Account"])
    type_col = find_col_name(df_sorted.columns, ["Transaction Type", "Type", "Buy/Sell", "Tipus"])
    ticker_col = find_col_name(df_sorted.columns, ["Symbol", "Ticker"])
    qty_col = find_col_name(df_sorted.columns, ["Quantity", "Shares", "Number", "Qty"])
    price_col = find_col_name(df_sorted.columns, ["Price", "Ar", "Unit Price"])
    curr_col = find_col_name(df_sorted.columns, ["Price Currency", "Currency", "Curr"])

    positions = {}
    realized_trades = []

    for _, row in df_sorted.iterrows():
        tx_type = str(row.get(type_col, "")).strip().title()
        
        if tx_type not in ["Buy", "Sell"]:
            continue

        raw_ticker = str(row.get(ticker_col, "")).strip().upper()
        
        if not raw_ticker or raw_ticker == "-" or raw_ticker.endswith(".HUF") or raw_ticker.endswith(".EUR"):
            continue
        
        ticker = ticker_map.get(raw_ticker, raw_ticker)

        broker = str(row.get(broker_col, "IBKR")).strip() or "IBKR"
        account = str(row.get(account_col, "")).strip()
        currency = str(row.get(curr_col, "EUR")).strip()

        qty = abs(clean_float(row.get(qty_col, 0)))
        unit_price = abs(clean_float(row.get(price_col, 0))) if price_col else 0.0
        
        native_amount = qty * unit_price
        
        net_huf = clean_float(row.get("_Net_Amount_HUF", 0))
        abs_cost_huf = abs(net_huf)

        key = (broker, account, ticker)
        if key not in positions:
            positions[key] = {
                "Broker": broker,
                "Account": account,
                "Ticker": ticker,
                "Shares": 0.0,
                "Total_Cost_Native": 0.0,
                "Total_Cost_HUF": 0.0,
                "Currency": currency,
            }

        pos = positions[key]

        if tx_type == "Buy":
            pos["Shares"] += qty
            pos["Total_Cost_Native"] += native_amount
            pos["Total_Cost_HUF"] += abs_cost_huf
        elif tx_type == "Sell" and pos["Shares"] > 0:
            current_wac_native = pos["Total_Cost_Native"] / pos["Shares"]
            current_wac_huf = pos["Total_Cost_HUF"] / pos["Shares"]

            cost_of_sold_native = qty * current_wac_native
            cost_of_sold_shares_huf = qty * current_wac_huf
            realized_pnl_huf = abs_cost_huf - cost_of_sold_shares_huf

            realized_trades.append({
                "Date": str(row.get(date_col, "N/A")),
                "Broker": broker,
                "Account": account,
                "Ticker": ticker,
                "Sold Shares": qty,
                "Proceeds HUF": abs_cost_huf,
                "Cost Basis HUF": cost_of_sold_shares_huf,
                "Realized PnL HUF": realized_pnl_huf,
            })

            pos["Shares"] -= qty
            pos["Total_Cost_Native"] -= cost_of_sold_native
            pos["Total_Cost_HUF"] -= cost_of_sold_shares_huf

            if pos["Shares"] <= 1e-6:
                pos["Shares"] = 0.0
                pos["Total_Cost_Native"] = 0.0
                pos["Total_Cost_HUF"] = 0.0

    active_list = [
        {
            "Broker": pos["Broker"],
            "Account": pos["Account"],
            "Ticker": pos["Ticker"],
            "Shares": pos["Shares"],
            "WAC Native": pos["Total_Cost_Native"] / pos["Shares"] if pos["Shares"] > 0 else 0.0,
            "Total Cost Native": pos["Total_Cost_Native"],
            "WAC HUF": pos["Total_Cost_HUF"] / pos["Shares"] if pos["Shares"] > 0 else 0.0,
            "Total Cost HUF": pos["Total_Cost_HUF"],
            "Currency": pos["Currency"],
        }
        for pos in positions.values()
        if pos["Shares"] > 0
    ]

    return pd.DataFrame(active_list), pd.DataFrame(realized_trades)


def enrich_with_live_prices(active_df, tax_map=None):
    if tax_map is None:
        tax_map = {}

    if active_df.empty:
        return active_df, 400.0

    tickers = active_df["Ticker"].unique().tolist()
    tickers_str = " ".join(tickers)
    batch_data = yf.download(
        tickers_str, period="5d", interval="1d", group_by="ticker", progress=False
    )

    eur_huf_rate = 400.0
    try:
        live_eur = yf.Ticker("EURHUF=X").fast_info.get("lastPrice")
        if live_eur and float(live_eur) > 0:
            eur_huf_rate = float(live_eur)
    except Exception:
        pass

    fx_cache = {}
    enriched = []

    for _, row in active_df.iterrows():
        ticker = row["Ticker"]
        shares = row["Shares"]
        total_cost_native = row["Total Cost Native"]
        total_cost_huf = row["Total Cost HUF"]
        curr = str(row["Currency"]).strip()

        currency_code, is_pence = validate_and_parse_currency(curr)

        live_price = 0.0
        try:
            if len(tickers) == 1:
                t_df = batch_data.dropna(subset=["Close"])
            else:
                t_df = batch_data[ticker].dropna(subset=["Close"])
            if not t_df.empty:
                live_price = float(t_df["Close"].iloc[-1])
        except Exception:
            pass

        if live_price > 0 and not is_pence and (ticker.endswith(".L") or currency_code == "GBP"):
            live_price = live_price / 100.0

        gross_div_yield = 0.0
        next_earnings = "N/A"
        ex_div_date = "N/A"
        t_info = {}
        try:
            t = yf.Ticker(ticker)
            t_info = t.info or {}

            raw_yield = t_info.get("dividendYield") or t_info.get("trailingAnnualDividendYield")
            if raw_yield is not None and float(raw_yield) > 0:
                raw_float = float(raw_yield)
                gross_div_yield = raw_float * 100.0 if raw_float < 1.0 else raw_float
            else:
                try:
                    divs = t.dividends
                    if divs is not None and not divs.empty:
                        cutoff = pd.Timestamp.now(tz=divs.index.tz) - pd.DateOffset(years=1)
                        ttm_divs = divs[divs.index >= cutoff]
                        if not ttm_divs.empty and live_price > 0:
                            div_sum = float(ttm_divs.sum())
                            gross_div_yield = (div_sum / live_price) * 100.0
                except Exception:
                    pass

            cal = t.calendar
            if cal is not None and isinstance(cal, dict) and "Earnings Date" in cal:
                next_earnings = str(cal["Earnings Date"][0])[:10]
            elif hasattr(cal, "get") and cal.get("Earnings Date") is not None:
                next_earnings = str(cal.get("Earnings Date"][0])[:10]

            raw_ex_div = t_info.get("exDividendDate")
            if raw_ex_div:
                if isinstance(raw_ex_div, (int, float)):
                    ex_div_date = datetime.fromtimestamp(raw_ex_div).strftime("%Y-%m-%d")
                else:
                    ex_div_date = str(raw_ex_div)[:10]
            elif cal is not None and isinstance(cal, dict) and "Ex-Dividend Date" in cal:
                ex_div_date = str(cal["Ex-Dividend Date"][0])[:10]
        except Exception:
            pass

        country_code = detect_country_code(ticker, t_info)
        w_tax_rate = tax_map.get(country_code, tax_map.get("UK" if country_code == "GB" else country_code, 0.0))
        net_div_yield = gross_div_yield * (1.0 - w_tax_rate)
        tax_pct = w_tax_rate * 100.0

        cache_key = (currency_code, is_pence)
        if cache_key not in fx_cache:
            fx_cache[cache_key] = get_live_fx_rate_to_huf(currency_code, is_pence, eur_huf_rate)
        
        rate_to_huf = fx_cache[cache_key]

        mkt_val_huf = shares * live_price * rate_to_huf
        pnl_huf = mkt_val_huf - total_cost_huf
        pnl_pct = (pnl_huf / total_cost_huf * 100.0) if total_cost_huf > 0 else 0.0

        enriched.append({
            "Broker": row["Broker"],
            "Account": row["Account"],
            "Ticker": ticker,
            "Shares": shares,
            "WAC Native": row["WAC Native"],
            "Total Cost Native": total_cost_native,
            "WAC HUF": row["WAC HUF"],
            "Live Price": live_price,
            "Currency": curr,
            "Cost HUF": total_cost_huf,
            "Market Value HUF": mkt_val_huf,
            "PnL HUF": pnl_huf,
            "PnL %": pnl_pct,
            "Market Value EUR": mkt_val_huf / eur_huf_rate,
            "PnL EUR": pnl_huf / eur_huf_rate,
            "Div Yield %": net_div_yield,
            "Gross Div Yield %": gross_div_yield,
            "Tax Rate %": tax_pct,
            "Country": country_code,
            "Next Earnings": next_earnings,
            "Next Ex-Div Date": ex_div_date,
        })

    return pd.DataFrame(enriched), eur_huf_rate


def get_consolidated_holdings(enriched_df, total_nav_huf=0.0):
    if enriched_df.empty:
        return pd.DataFrame()

    grouped = enriched_df.groupby("Ticker")

    consolidated = []
    for ticker, group in grouped:
        total_shares = group["Shares"].sum()
        total_cost_native = group["Total Cost Native"].sum()
        mkt_val_huf = group["Market Value HUF"].sum()

        first_row = group.iloc[0]
        live_price = first_row["Live Price"]
        curr = first_row["Currency"]

        avg_cost_native = total_cost_native / total_shares if total_shares > 0 else 0.0
        pnl_pct_native = ((live_price - avg_cost_native) / avg_cost_native * 100.0) if avg_cost_native > 0 else 0.0
        weight_pct = (mkt_val_huf / total_nav_huf * 100.0) if total_nav_huf > 0 else 0.0

        accounts = ", ".join(sorted(group["Account"].unique().tolist()))
        brokers = ", ".join(sorted(group["Broker"].unique().tolist()))

        net_div_yield = first_row["Div Yield %"]
        gross_div_yield = first_row.get("Gross Div Yield %", net_div_yield)
        tax_pct = first_row.get("Tax Rate %", 0.0)

        if avg_cost_native > 0 and live_price > 0:
            net_yoc = net_div_yield * (live_price / avg_cost_native)
        else:
            net_yoc = 0.0

        yield_display = f"{net_div_yield:.2f}% - {net_yoc:.2f}%"

        next_earnings = first_row["Next Earnings"]
        ex_div_date = first_row.get("Next Ex-Div Date", "N/A")

        consolidated.append({
            "Ticker": ticker,
            "Total Shares": total_shares,
            "Live Price": live_price,
            "Currency": curr,
            "Avg Cost": avg_cost_native,
            "PnL %": pnl_pct_native,
            "Weight %": weight_pct,
            "Net Div Yield % (Live - Cost)": yield_display,
            "Net Div Yield %": net_div_yield,
            "Net YoC %": net_yoc,
            "Gross Div Yield %": gross_div_yield,
            "Tax Rate %": tax_pct,
            "Next Earnings": next_earnings,
            "Next Ex-Div Date": ex_div_date,
            "Accounts": accounts,
            "Brokers": brokers,
        })

    df_res = pd.DataFrame(consolidated)
    if not df_res.empty:
        df_res = df_res.sort_values(by="Weight %", ascending=False)
    return df_res


def calculate_cash_and_nav(df_tx, enriched_df, selected_broker="All Brokers", selected_account="All Accounts"):
    tx_filt = df_tx.copy() if not df_tx.empty else pd.DataFrame()
    hold_filt = enriched_df.copy() if not enriched_df.empty else pd.DataFrame()

    if selected_broker != "All Brokers" and not tx_filt.empty:
        tx_filt = tx_filt[tx_filt["Broker"].astype(str).str.strip() == selected_broker]
        if not hold_filt.empty:
            hold_filt = hold_filt[hold_filt["Broker"].astype(str).str.strip() == selected_broker]

    if selected_account != "All Accounts" and not tx_filt.empty:
        account_col = find_col_name(tx_filt.columns, ["Account"])
        if account_col:
            tx_filt = tx_filt[tx_filt[account_col].astype(str).str.strip() == selected_account]
        if not hold_filt.empty:
            hold_filt = hold_filt[hold_filt[account_col].astype(str).str.strip() == selected_account]

    total_deposits_huf = 0.0
    cash_balance_huf = 0.0
    xirr_cash_flows = []
    xirr_dates = []

    date_col = find_col_name(tx_filt.columns, ["Date", "Tx Date", "Datum"]) if not tx_filt.empty else None
    type_col = find_col_name(tx_filt.columns, ["Transaction Type", "Type", "Buy/Sell", "Tipus"]) if not tx_filt.empty else None

    if not tx_filt.empty and "_Net_Amount_HUF" in tx_filt.columns:
        for _, r in tx_filt.iterrows():
            net_huf = clean_float(r.get("_Net_Amount_HUF", 0))
            cash_balance_huf += net_huf

            if type_col:
                t_type = str(r.get(type_col, "")).strip().title()
                tx_dt = parse_date_string(r.get(date_col, datetime.now())) if date_col else datetime.now()

                if t_type == "Deposit":
                    total_deposits_huf += net_huf
                    xirr_cash_flows.append(-abs(net_huf))
                    xirr_dates.append(tx_dt)
                elif t_type == "Withdrawal":
                    total_deposits_huf -= abs(net_huf)
                    xirr_cash_flows.append(abs(net_huf))
                    xirr_dates.append(tx_dt)

    invested_mkt_val_huf = 0.0
    expected_div_huf = 0.0
    if not hold_filt.empty and "Market Value HUF" in hold_filt.columns:
        invested_mkt_val_huf = float(hold_filt["Market Value HUF"].apply(clean_float).sum())
        if "Div Yield %" in hold_filt.columns:
            expected_div_huf = float(
                (
                    hold_filt["Market Value HUF"].apply(clean_float)
                    * (hold_filt["Div Yield %"].apply(clean_float) / 100.0)
                ).sum()
            )

    net_div_yield_pct = (expected_div_huf / invested_mkt_val_huf * 100.0) if invested_mkt_val_huf > 0 else 0.0

    total_nav_huf = cash_balance_huf + invested_mkt_val_huf
    total_net_gain_huf = total_nav_huf - total_deposits_huf if total_deposits_huf > 0 else total_nav_huf
    net_return_pct = (
        (total_net_gain_huf / total_deposits_huf * 100.0)
        if total_deposits_huf > 0
        else 0.0
    )

    if total_nav_huf > 0 and len(xirr_cash_flows) > 0:
        xirr_cash_flows.append(total_nav_huf)
        xirr_dates.append(datetime.now())

    annualized_xirr = calculate_xirr(xirr_cash_flows, xirr_dates)

    return {
        "Deposits HUF": total_deposits_huf,
        "Cash Balance HUF": cash_balance_huf,
        "Invested HUF": invested_mkt_val_huf,
        "Total NAV HUF": total_nav_huf,
        "Net Gain HUF": total_net_gain_huf,
        "Net Return %": net_return_pct,
        "Annualized XIRR %": annualized_xirr,
        "Expected Dividend HUF": expected_div_huf,
        "Expected Net Yield %": net_div_yield_pct,
    }


def get_breakdown_summary(df_tx, enriched_df, eur_huf_rate):
    if df_tx.empty:
        return pd.DataFrame()

    account_col = find_col_name(df_tx.columns, ["Account"])
    
    tx_pairs = set(zip(df_tx["Broker"].astype(str).str.strip(), df_tx[account_col].astype(str).str.strip())) if account_col else set()
    hold_pairs = set(zip(enriched_df["Broker"].astype(str).str.strip(), enriched_df["Account"].astype(str).str.strip())) if not enriched_df.empty else set()
    all_pairs = sorted(list(tx_pairs.union(hold_pairs)))

    summary_rows = []
    for broker, account in all_pairs:
        stats = calculate_cash_and_nav(df_tx, enriched_df, selected_broker=broker, selected_account=account)
        summary_rows.append({
            "Broker": broker,
            "Account": account,
            "Deposits (HUF)": stats["Deposits HUF"],
            "Cash Balance (HUF)": stats["Cash Balance HUF"],
            "Invested Market Value (HUF)": stats["Invested HUF"],
            "Total NAV (HUF)": stats["Total NAV HUF"],
            "Expected Dividend (HUF)": stats["Expected Dividend HUF"],
            "Expected Net Yield %": stats["Expected Net Yield %"],
            "Net Return %": stats["Net Return %"],
            "Annualized XIRR %": stats["Annualized XIRR %"],
            "Total NAV (EUR)": stats["Total NAV HUF"] / eur_huf_rate,
            "Expected Dividend (EUR)": stats["Expected Dividend HUF"] / eur_huf_rate,
        })

    return pd.DataFrame(summary_rows)


def scrape_official_ir_news(ticker: str, url: str):
    articles = []
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/115.0.0.0 Safari/537.36"
        )
    }

    try:
        res = requests.get(url, headers=headers, timeout=10, verify=False)
        if res.status_code != 200:
            return articles

        soup = BeautifulSoup(res.text, "html.parser")
        parsed_url = urlparse(url)
        domain = parsed_url.netloc.replace("www.", "")

        anchors = soup.find_all("a", href=True)
        seen_links = set()

        for a in anchors:
            title = a.get_text(strip=True)
            if not title or len(title) < 15:
                continue

            # Ignore generic navigation text
            title_lower = title.lower()
            if any(
                bad in title_lower
                for bad in [
                    "read more", "cookies", "privacy", "contact", "home",
                    "subscribe", "next", "previous", "download", "pdf",
                    "search", "menu", "login", "register"
                ]
            ):
                continue

            href = a["href"].strip()
            full_link = urljoin(url, href)

            if full_link in seen_links:
                continue
            seen_links.add(full_link)

            articles.append({
                "Ticker": ticker,
                "Title": title,
                "Url": full_link,
                "Domain": domain,
                "Source": "Official IR",
            })

            if len(articles) >= 10:
                break
    except Exception:
        pass

    return articles


def fetch_portfolio_news(active_tickers, ir_url_map):
    if not active_tickers:
        return pd.DataFrame()

    all_articles = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_ticker = {}
        for ticker in active_tickers:
            url = ir_url_map.get(ticker, "")
            if url:
                future = executor.submit(scrape_official_ir_news, ticker, url)
                future_to_ticker[future] = ticker

        for future in concurrent.futures.as_completed(future_to_ticker):
            res = future.result()
            if res:
                all_articles.extend(res)

    if not all_articles:
        return pd.DataFrame()

    return pd.DataFrame(all_articles)
