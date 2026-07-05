"""Streamlit dashboard: reads signal/position state from SQLite (written by
worker.py) and lets the user place/close paper trades. The only exception to
"reads only from SQLite" is placing a trade itself, which needs one live
quote to record an accurate entry price - a user-triggered action, not a
background fetch.
"""
import json

import streamlit as st

from config import load_settings
from data import market_data
from paper_trading.engine import calculate_contracts, calculate_pnl, close as close_position
from paper_trading.engine import buy as buy_position
from storage import db as storage

st.set_page_config(page_title="0DTE Paper Trading", layout="centered")

config = load_settings()
db_path = config["database"]["path"]
storage.init_db(db_path)
storage.ensure_account(db_path, config["account"]["starting_balance"])

st.title("0DTE Paper Trading")

balance = storage.get_balance(db_path)
st.metric("Virtual account balance", f"${balance:,.2f}")

ticker = st.selectbox("Ticker", config["tickers"])

st.header(f"Signal: {ticker}")
snapshot = storage.get_latest_signal(db_path, ticker)

if snapshot is None:
    st.info("No signal yet - make sure worker.py is running.")
else:
    direction = snapshot["direction"]
    confidence = snapshot["confidence"]
    color = {"bullish": "green", "bearish": "red", "neutral": "gray"}.get(direction, "gray")
    st.markdown(f"**Direction:** :{color}[{direction.upper()}]")
    st.markdown(f"**Confidence:** {confidence:.0f}%")
    st.markdown(f"**Recommendation:** {snapshot['recommendation']}")
    st.caption(f"as of {snapshot['timestamp']}")

    with st.expander("Signal breakdown"):
        st.json(json.loads(snapshot["subscores_json"]))

    st.subheader("Place a paper trade")
    option_type = "call" if direction == "bullish" else "put"
    dollar_amount = st.number_input(
        "Amount to risk ($)", min_value=0.0,
        value=balance * config["account"]["risk_per_trade_pct"] / 100.0, step=50.0,
    )

    if st.button(f"Buy 0DTE {option_type}s on {ticker}"):
        chain = market_data.get_option_chain(ticker)
        if chain is None:
            st.error("Couldn't fetch a live quote right now - try again in a moment.")
        else:
            df = chain.calls if option_type == "call" else chain.puts
            contract = market_data.find_atm_contract(df, chain.spot)
            if contract is None:
                st.error("No option contract available for this ticker right now.")
            else:
                entry_price = float(contract["lastPrice"])
                contracts = calculate_contracts(dollar_amount, 100.0, entry_price)
                if contracts <= 0:
                    st.warning("That amount isn't enough for even one contract at the current price.")
                else:
                    position_id = buy_position(
                        db_path, ticker, option_type, float(contract["strike"]),
                        chain.expiration, entry_price, contracts,
                        snapshot["composite_score"],
                    )
                    if position_id:
                        st.success(f"Bought {contracts} {ticker} {contract['strike']} {option_type}"
                                   f" @ ${entry_price:.2f}")
                        st.rerun()
                    else:
                        st.error("Trade rejected - insufficient balance.")

st.header("Open positions")
open_positions = storage.get_open_positions(db_path)
if not open_positions:
    st.write("No open positions.")
else:
    for pos in open_positions:
        with st.container(border=True):
            st.write(f"**{pos['ticker']} {pos['strike']} {pos['option_type']}** "
                      f"x{pos['contracts']} @ ${pos['entry_price']:.2f} "
                      f"(exp {pos['expiration']})")
            if pos["current_price"] is not None:
                pnl_dollars, pnl_pct = calculate_pnl(pos["entry_price"], pos["current_price"], pos["contracts"])
                st.write(f"Current: ${pos['current_price']:.2f} | "
                          f"Unrealized P&L: ${pnl_dollars:,.2f} ({pnl_pct:+.1f}%)")
            if pos["suggested_exit_reason"]:
                st.warning(f"SUGGESTED EXIT: {pos['suggested_exit_reason']}")
            if st.button("Close position", key=f"close_{pos['id']}"):
                exit_price = pos["current_price"] if pos["current_price"] is not None else pos["entry_price"]
                reason = pos["suggested_exit_reason"] or "manual"
                close_position(db_path, pos["id"], exit_price, reason)
                st.rerun()

st.header("Trade history")
closed_positions = storage.get_closed_positions(db_path)
if not closed_positions:
    st.write("No closed trades yet.")
else:
    st.dataframe([
        {
            "ticker": p["ticker"], "type": p["option_type"], "strike": p["strike"],
            "entry": p["entry_price"], "exit": p["exit_price"], "pnl": p["pnl"],
            "reason": p["exit_reason"], "closed": p["exit_time"],
        }
        for p in closed_positions
    ])
