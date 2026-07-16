"""Streamlit dashboard: reads signal/position state from SQLite (written by
worker.py) and lets the user place/close paper trades. The only exception to
"reads only from SQLite" is placing a trade itself, which needs one live
quote to record an accurate entry price - a user-triggered action, not a
background fetch.

Layout: a top status banner, a wide left column for the signal/accuracy view,
and a persistent right column (a plain st.columns split, not st.sidebar) for
account balance, trade placement, and position history - always visible,
never collapsed.
"""
import calendar as _cal
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from analytics import accuracy
from config import load_settings
from data import market_data
from paper_trading.engine import calculate_contracts, calculate_pnl, close as close_position
from paper_trading.engine import (
    buy as buy_position, daily_realized_pnl, month_calendar_cells, price_target_exit,
    shift_month, summarize_pnl, trade_events,
)
from paper_trading.models import Position
from paper_trading.shadow import strategy_edge, strategy_scorecard
from storage import db as storage
from worker import (
    is_market_open, minutes_since_market_open, minutes_to_market_close, next_trading_day,
)

st.set_page_config(page_title="0DTE Paper Trading", layout="wide")

CATEGORY_INFO = {
    "technicals": ("Technicals", "Is the short-term price trend pointing up or down right now "
                                  "(momentum, RSI, price vs. volume-weighted average)."),
    "greeks_iv": ("Options pricing (IV skew)", "Are options traders paying more for calls or puts "
                                                "right now - a bet on which way price goes."),
    "order_flow": ("Order flow", "Are more contracts trading as calls or puts, and where would "
                                  "price 'settle' to hurt the most option holders (max pain)."),
    "sentiment": ("Social sentiment", "What retail traders are saying right now on Reddit and StockTwits."),
    "prediction_markets": ("Prediction markets", "What real-money bettors on Kalshi think the odds "
                                                  "are of the index finishing higher today."),
    "volatility_regime": ("Volatility regime", "Is the overall market calm or fearful right now "
                                                "(VIX/VVIX) - fear tends to precede lower prices."),
    "trump_news": ("Trump / political headlines", "How much tariff, trade, and Fed-related news "
                                                    "involving Trump is in the cycle right now, and "
                                                    "whether the tone leans bearish or bullish."),
}

DIRECTION_COLOR = {"bullish": "#2ecc71", "bearish": "#e74c3c", "neutral": "#95a5a6"}
DIRECTION_ICON = {"bullish": "trending_up", "bearish": "trending_down", "neutral": "trending_flat"}
# per-trade profit-target / stop-loss choices; None = that auto-exit is off
EXIT_PCT_OPTIONS = [None, 5, 10, 20, 30, 40, 50]

config = load_settings()
db_path = config["database"]["path"]
storage.init_db(db_path)
storage.ensure_account(db_path, config["account"]["starting_balance"])
storage.ensure_worker_settings(db_path, config["poll_interval_minutes"] * 60)
storage.ensure_autopilot(db_path)
storage.ensure_calibration(db_path)


def _display_confidence(snap) -> tuple[float, float | None]:
    """(shown, raw_if_calibrated): the value to display is the worker's stored
    calibrated_confidence when present, else the raw confidence. The second
    element is the raw value only when it differs (for a 'calibrated from N%'
    caption), else None."""
    raw = snap["confidence"]
    calibrated = snap["calibrated_confidence"]
    if calibrated is not None and calibrated != raw:
        return calibrated, raw
    return raw, None

# Weight overrides are per-ticker, so the "active weights" are resolved where the
# selected ticker is known (inside the signal-card and accuracy fragments), not here.


@st.cache_data(ttl=120)
def _accuracy_analysis(rows_tuple: tuple, horizon_minutes: float):
    """The O(n^2) accuracy grading, cached so the auto-refreshing accuracy panel
    doesn't recompute it every tick. Keyed on rows_tuple (a hashable fingerprint
    of the history) so it only truly recomputes when a new snapshot lands."""
    snapshots = [
        {
            "timestamp": datetime.fromisoformat(ts),
            "direction": direction,
            "spot_price": spot_price,
            "confidence": confidence,
            "composite_score": composite_score,
            "subscores": json.loads(subscores_json),
            "direction_streak": direction_streak,
        }
        for ts, direction, spot_price, confidence, composite_score, subscores_json,
            direction_streak in rows_tuple
    ]
    evaluated = accuracy.evaluate_signal_accuracy(snapshots, horizon_minutes)
    category_results = accuracy.evaluate_category_accuracy(snapshots, horizon_minutes)
    calibration = accuracy.confidence_calibration(evaluated)
    return snapshots, evaluated, category_results, calibration


st.markdown(
    """
    <style>
    .st-key-signal_panel, .st-key-accuracy_panel, .st-key-day_setup_card, .st-key-strategy_lab_card,
    .st-key-wallet_card, .st-key-calendar_card, .st-key-order_card,
    .st-key-positions_card, .st-key-history_card {
        border-radius: 12px;
        padding: 1.25rem 1.5rem;
        margin-bottom: 1.5rem;
    }
    /* Fallback based on OS preference, used only until the script below
       determines the app's *actual* in-app theme (which the user can set
       independently of their OS via the Streamlit menu). */
    @media (prefers-color-scheme: light) {
        .st-key-signal_panel, .st-key-accuracy_panel, .st-key-day_setup_card, .st-key-strategy_lab_card,
        .st-key-wallet_card, .st-key-calendar_card, .st-key-order_card,
        .st-key-positions_card, .st-key-history_card { background-color: #f7f8fa; }
    }
    @media (prefers-color-scheme: dark) {
        .st-key-signal_panel, .st-key-accuracy_panel, .st-key-day_setup_card, .st-key-strategy_lab_card,
        .st-key-wallet_card, .st-key-calendar_card, .st-key-order_card,
        .st-key-positions_card, .st-key-history_card { background-color: #191c24; }
    }
    /* Higher-specificity rules driven by the detected in-app theme - win over
       the media-query fallback above once data-app-theme is set. */
    html[data-app-theme="light"] .st-key-signal_panel,
    html[data-app-theme="light"] .st-key-accuracy_panel,
    html[data-app-theme="light"] .st-key-day_setup_card,
    html[data-app-theme="light"] .st-key-strategy_lab_card,
    html[data-app-theme="light"] .st-key-wallet_card,
    html[data-app-theme="light"] .st-key-calendar_card,
    html[data-app-theme="light"] .st-key-order_card,
    html[data-app-theme="light"] .st-key-positions_card,
    html[data-app-theme="light"] .st-key-history_card {
        background-color: #f7f8fa;
    }
    html[data-app-theme="dark"] .st-key-signal_panel,
    html[data-app-theme="dark"] .st-key-accuracy_panel,
    html[data-app-theme="dark"] .st-key-day_setup_card,
    html[data-app-theme="dark"] .st-key-strategy_lab_card,
    html[data-app-theme="dark"] .st-key-wallet_card,
    html[data-app-theme="dark"] .st-key-calendar_card,
    html[data-app-theme="dark"] .st-key-order_card,
    html[data-app-theme="dark"] .st-key-positions_card,
    html[data-app-theme="dark"] .st-key-history_card {
        background-color: #191c24;
    }

    /* Confidence progress bar tinted by direction - Streamlit doesn't expose
       a per-instance color prop for st.progress, so this keys off the same
       key-derived class trick used for the shaded cards above. */
    div[class*="st-key-confidence_bullish"] [data-testid="stProgress"] div[role="progressbar"] > div {
        background-color: #2ecc71 !important;
    }
    div[class*="st-key-confidence_bearish"] [data-testid="stProgress"] div[role="progressbar"] > div {
        background-color: #e74c3c !important;
    }
    div[class*="st-key-confidence_neutral"] [data-testid="stProgress"] div[role="progressbar"] > div {
        background-color: #95a5a6 !important;
    }

    /* Open-position cards tinted by live P&L sign. Each position gets a
       unique key (position_win_<id> / position_loss_<id>), matched here with
       a substring selector since the exact suffix varies per position. */
    div[class*="st-key-position_win_"] {
        border-color: #2ecc71 !important;
        background-color: rgba(46, 204, 113, 0.07);
    }
    div[class*="st-key-position_loss_"] {
        border-color: #e74c3c !important;
        background-color: rgba(231, 76, 60, 0.07);
    }

    /* Buy-call / buy-put buttons get a colored border only when that side
       matches the current signal - the border itself is the highlight,
       no separate "Suggested" label needed. */
    div[class*="st-key-buy_call_suggested"] button {
        border-color: #2ecc71 !important;
        color: #2ecc71 !important;
    }
    div[class*="st-key-buy_put_suggested"] button {
        border-color: #e74c3c !important;
        color: #e74c3c !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Streamlit's in-app theme switcher (menu -> Settings -> theme) is independent of
# the OS/browser dark-mode setting our CSS media queries key off. This detects the
# *actual* rendered theme by reading the app's background color and tags <html>
# with it, then keeps watching for switches so the card shading always matches.
components.html(
    """
    <script>
    (function() {
        function applyTheme() {
            try {
                const app = window.parent.document.querySelector('.stApp');
                if (!app) return;
                const rgb = getComputedStyle(app).backgroundColor.match(/\\d+/g);
                if (!rgb) return;
                const [r, g, b] = rgb.map(Number);
                const luminance = 0.299 * r + 0.587 * g + 0.114 * b;
                window.parent.document.documentElement.setAttribute(
                    'data-app-theme', luminance > 128 ? 'light' : 'dark'
                );
            } catch (e) {}
        }
        applyTheme();
        const app = window.parent.document.querySelector('.stApp');
        if (app) {
            new MutationObserver(applyTheme).observe(app, {attributes: true, attributeFilter: ['class', 'style']});
        }
    })();
    </script>
    """,
    height=0,
)

# Worker poll cadence is fixed at 1 minute (was a user-facing selector; 1 min is
# the sweet spot - fast enough to be useful, slow enough to avoid rate-limiting).
if storage.get_poll_interval_seconds(db_path) != 60:
    storage.set_poll_interval_seconds(db_path, 60)


@st.fragment(run_every="30s")
def render_close_progress() -> None:
    """A thin fixed bar pinned to the very top of the viewport tracking the
    trading session's progress: empty at the open, full as the close nears.
    Muted/empty when the market is closed. Colors warm up in the final hour."""
    since_open = minutes_since_market_open(config)
    to_close = minutes_to_market_close(config)
    session_minutes = since_open + to_close  # == close - open, constant all day
    open_now = is_market_open(config)

    if open_now and session_minutes > 0:
        fraction = max(0.0, min(1.0, since_open / session_minutes))
        if to_close <= 30:
            fill = "#e74c3c"      # last half hour - urgent
        elif to_close <= 60:
            fill = "#f0a202"      # last hour - warm
        else:
            fill = "#3b82f6"      # plenty of runway
        hrs, mins = divmod(int(max(0, to_close)), 60)
        tip = f"{hrs}h {mins}m to market close" if hrs else f"{mins}m to market close"
    else:
        fraction = 0.0
        fill = "#3b82f6"
        tip = "Market closed"

    st.markdown(
        f"""
        <div title="{tip}" style="
            position: fixed; top: 0; left: 0; width: 100%; height: 3px;
            background: rgba(128,128,128,0.18); z-index: 999999; pointer-events: auto;
        ">
            <div style="width: {fraction * 100:.2f}%; height: 100%;
                        background: {fill}; transition: width 0.6s ease;"></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


render_close_progress()


def _signal_age(snap) -> timedelta:
    return datetime.now(timezone.utc) - datetime.fromisoformat(snap["timestamp"])


def _age_text(age: timedelta) -> str:
    minutes = int(age.total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    return f"{minutes // 60}h {minutes % 60}m ago"


def render_signal_freshness(snap) -> None:
    """Relative-age caption, escalating to a warning if the worker looks dead
    while the market is open. Outside market hours staleness is expected."""
    age = _signal_age(snap)
    stale_after = max(3 * storage.get_poll_interval_seconds(db_path), 900)
    market_tz = ZoneInfo(config["market_hours"].get("timezone", "America/New_York"))
    local_time = datetime.fromisoformat(snap["timestamp"]).astimezone(market_tz)

    if age.total_seconds() <= stale_after:
        st.caption(f"Updated {_age_text(age)}")
    elif is_market_open(config):
        st.warning(f"Last signal is from {_age_text(age)} - the worker may not be running.",
                   icon=":material/warning:")
    else:
        st.caption(f"Market closed - last signal {local_time.strftime('%a %H:%M')} ET "
                   f"({_age_text(age)})")


# --- Top status banners, one per ticker (live) -----------------------------
@st.fragment(run_every="30s")
def render_status_banners(selected_ticker: str) -> None:
    columns = st.columns(len(config["tickers"]))
    for column, banner_ticker in zip(columns, config["tickers"]):
        snap = storage.get_latest_signal(db_path, banner_ticker)
        with column:
            if snap is None:
                st.caption(f"{banner_ticker}: no signal yet")
                continue
            direction = snap["direction"]
            confidence, _raw_conf = _display_confidence(snap)
            banner_color = DIRECTION_COLOR.get(direction, "#95a5a6")
            banner_icon = DIRECTION_ICON.get(direction, "trending_flat")
            # short action line, derived from the full recommendation phrasing
            rec = snap["recommendation"].lower()
            if "stay out" in rec or "no clear edge" in rec:
                action = "Stay out"
            elif "calls" in rec:
                action = "Consider buying calls"
            elif "puts" in rec:
                action = "Consider buying puts"
            else:
                action = ""
            # the selected ticker's banner reads as "active" via a heavier border
            border = f"2px solid {banner_color}" if banner_ticker == selected_ticker \
                else f"1px solid {banner_color}66"
            st.markdown(
                f"""
                <div style="
                    background-color: {banner_color}22;
                    border: {border};
                    border-radius: 10px;
                    padding: 0.6rem 0.9rem;
                    margin-bottom: 1.25rem;
                    display: flex;
                    align-items: center;
                    gap: 0.6rem;
                ">
                    <span style="
                        font-family: 'Material Symbols Rounded';
                        color: {banner_color};
                        font-size: 1.5rem;
                        line-height: 1;
                    ">
                        {banner_icon}
                    </span>
                    <div style="line-height: 1.35;">
                        <div style="font-size: 1.0rem; font-weight: 600;">
                            {banner_ticker} &middot; {direction.upper()}
                        </div>
                        <div style="font-size: 0.85rem; opacity: 0.9;">{confidence:.0f}% confidence{f" (from {_raw_conf:.0f}% raw)" if _raw_conf is not None else ""}</div>
                        <div style="font-size: 0.85rem; opacity: 0.85;">{action}</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )


@st.fragment(run_every="15s")
def render_live_price(selected_ticker: str) -> None:
    """Fetches a fresh quote directly (independent of the worker's poll cadence)
    so price movement is visible without needing to touch the page."""
    price = market_data.get_current_price(selected_ticker)
    now_str = datetime.now().strftime("%H:%M:%S")
    if price is None:
        st.caption(f"Live price unavailable right now (last checked {now_str}).")
    else:
        st.caption(f":material/radio_button_checked: Live {selected_ticker}: "
                   f"**${price:,.2f}** &middot; updated {now_str}")


# Split into left/right at the very top so the right panel spans the full page
# height alongside the signal view, rather than starting below the banners.
left_col, right_col = st.columns([2, 1], gap="large")

with left_col:
    ticker = st.segmented_control("Ticker", config["tickers"], default=config["tickers"][0])
    if not ticker:
        ticker = config["tickers"][0]

    render_status_banners(ticker)
    render_live_price(ticker)

    with st.container(key="signal_panel"):
        st.header(f":material/monitoring: Signal: {ticker}")

        @st.fragment(run_every="30s")
        def render_signal_card() -> None:
            snap = storage.get_latest_signal(db_path, ticker)
            if snap is None:
                st.info("No signal yet - make sure worker.py is running.")
                return
            direction = snap["direction"]
            confidence, raw_conf = _display_confidence(snap)

            col_direction, col_confidence = st.columns(2)
            col_direction.metric("Direction", direction.upper())
            with col_confidence:
                st.caption("Confidence")
                with st.container(key=f"confidence_{direction}"):
                    st.progress(min(confidence / 100.0, 1.0), text=f"{confidence:.0f}%")
                if raw_conf is not None:
                    st.caption(f":material/tune: calibrated from {raw_conf:.0f}% raw")

            # dealer-gamma regime: a rangebound-vs-trending hint, not a direction
            gamma_regime = snap["gamma_regime"]
            if gamma_regime:
                gamma_score = snap["gamma_score"]
                _GAMMA_BADGE = {
                    "positive": ("Long gamma · rangebound",
                                 "dealers fade moves; breakouts tend to fail, so auto-pilot "
                                 "wants extra confidence here", "#f0a202"),
                    "negative": ("Short gamma · trending",
                                 "dealers chase moves; breakouts tend to follow through", "#2ecc71"),
                    "neutral": ("Neutral gamma",
                                "no strong dealer-hedging lean either way", "#95a5a6"),
                }
                label, blurb, color = _GAMMA_BADGE[gamma_regime]
                score_txt = f" ({gamma_score:+.2f})" if gamma_score is not None else ""
                st.markdown(
                    f"<span style='color:{color}; font-weight:600;'>● {label}{score_txt}</span>",
                    unsafe_allow_html=True,
                )
                st.caption(f"Dealer gamma: {blurb}. Approximate (front-expiry).")

            # how long this call has held - the composite itself is memoryless,
            # so a 1-min blip and a 40-min conviction otherwise look identical
            streak = snap["direction_streak"]
            if streak and direction != "neutral":
                poll_min = max(1, storage.get_poll_interval_seconds(db_path) // 60)
                st.caption(f":material/timelapse: {direction} for {streak * poll_min} min "
                           f"({streak} consecutive polls)")
            render_signal_freshness(snap)

            with st.expander("How is this score calculated? (ELI5)"):
                st.markdown(
                    "Each signal below is scored from **-1 (bearish) to +1 (bullish)**, then combined "
                    "into one weighted average. If a source has no data right now, the others simply "
                    "share its weight so the overall signal still works."
                )
                subscores = json.loads(snap["subscores_json"])
                active_weights = storage.effective_weights(db_path, config, ticker)
                st.dataframe(
                    [
                        {
                            "Signal": CATEGORY_INFO.get(key, (key, ""))[0],
                            "What it means": CATEGORY_INFO.get(key, (key, ""))[1],
                            "Weight": f"{active_weights.get(key, 0) * 100:.0f}%",
                            "Current reading": f"{value:+.2f}",
                        }
                        for key, value in subscores.items()
                    ],
                    hide_index=True,
                    use_container_width=True,
                )

        render_signal_card()

    with st.container(key="day_setup_card"):
        st.subheader(":material/wb_twilight: Pre-market day setup")

        @st.fragment(run_every="300s")
        def render_day_setup() -> None:
            market_tz_ds = ZoneInfo(config["market_hours"].get("timezone", "America/New_York"))
            today_ds = datetime.now(market_tz_ds).date().isoformat()
            setup = storage.get_day_setup(db_path, ticker, today_ds)
            if setup is None:
                st.caption("No pre-market setup yet - it's assembled in the "
                           "~90 min before the open.")
                return

            gap = setup.get("gap_pct")
            if gap is not None:
                arrow = "▲" if gap > 0 else ("▼" if gap < 0 else "▬")
                color = "#2ecc71" if gap > 0 else ("#e74c3c" if gap < 0 else "#95a5a6")
                bias = setup.get("opening_bias")
                bias_txt = f" · opening bias {bias:+.2f}" if bias is not None else ""
                st.markdown(
                    f"<div style='font-size:1.05rem;'>Overnight gap "
                    f"<span style='color:{color}; font-weight:600;'>{arrow} {gap:+.2f}%</span>"
                    f"<span style='opacity:0.75;'>{bias_txt}</span></div>",
                    unsafe_allow_html=True,
                )
            else:
                st.caption("Overnight gap unavailable.")

            def _lvl(v):
                return f"{v:,.2f}" if isinstance(v, (int, float)) else "—"

            cols = st.columns(3)
            cols[0].metric("Prior close", _lvl(setup.get("prior_close")))
            cols[1].metric("Prior high", _lvl(setup.get("prior_high")))
            cols[2].metric("Prior low", _lvl(setup.get("prior_low")))
            on_hi, on_lo = setup.get("overnight_high"), setup.get("overnight_low")
            if on_hi is not None or on_lo is not None:
                st.caption(f":material/nightlight: Overnight range: "
                           f"{_lvl(on_lo)} – {_lvl(on_hi)}")

            catalysts = setup.get("catalysts") or []
            if catalysts:
                items = ", ".join(
                    f"**{c['label']}**" + (f" @ {c['time']} ET" if c.get("time") else "")
                    for c in catalysts
                )
                st.warning(f":material/event: Today's catalysts: {items} — auto-pilot stands "
                           "down nearby.", icon=":material/warning:")
            else:
                st.caption(":material/event_available: No scheduled catalysts today.")

        render_day_setup()

    with st.container(key="accuracy_panel"):
        st.header(f":material/track_changes: Signal history & accuracy: {ticker}")

        @st.fragment(run_every="60s")
        def render_accuracy_panel() -> None:
          history_rows = storage.get_signal_history(db_path, ticker)
          if not history_rows:
            st.write("Not enough history yet - check back after a few poll cycles.")
          else:
            # re-read this ticker's active weights inside the fragment so an
            # applied/reverted override is reflected even on a fragment-only rerun
            weight_override = storage.get_weight_overrides(db_path, ticker)
            active_weights = weight_override[0] if weight_override is not None else config["weights"]

            horizon_minutes = config["accuracy_horizon_minutes"]
            rows_tuple = tuple(
                (row["timestamp"], row["direction"], row["spot_price"], row["confidence"],
                 row["composite_score"], row["subscores_json"], row["direction_streak"])
                for row in history_rows
            )
            snapshots, evaluated, category_results, calibration = _accuracy_analysis(
                rows_tuple, horizon_minutes)

            # Range filters the chart only - the accuracy stats below always
            # cover full history, since they measure long-run performance.
            range_choice = st.segmented_control(
                "Chart range", ["Today", "Last 4h", "3 days", "All"], default="Today",
                key="chart_range",
            ) or "Today"
            market_tz = ZoneInfo(config["market_hours"].get("timezone", "America/New_York"))
            now_market = datetime.now(market_tz)
            # "Today" and "Last 4h" pin the x-axis to a fixed window with 2h of
            # headroom on the right (naive market-local wall-clock, matching the
            # data conversion below); the other ranges auto-fit to the data.
            x_scale = alt.Scale()
            if range_choice == "Today":
                open_h, open_m = (int(v) for v in config["market_hours"]["open"].split(":"))
                cutoff = now_market.replace(hour=open_h, minute=open_m, second=0, microsecond=0)
                x_scale = alt.Scale(domain=[
                    cutoff.replace(tzinfo=None),
                    (now_market + timedelta(hours=2)).replace(tzinfo=None),
                ])
            elif range_choice == "Last 4h":
                cutoff = now_market - timedelta(hours=4)
                x_scale = alt.Scale(domain=[
                    cutoff.replace(tzinfo=None),
                    (now_market + timedelta(hours=2)).replace(tzinfo=None),
                ])
            elif range_choice == "3 days":
                cutoff = now_market - timedelta(days=3)
            else:
                cutoff = None

            chart_rows = [
                s for s in snapshots
                if s["spot_price"] is not None and (cutoff is None or s["timestamp"] >= cutoff)
            ]
            chart_df = pd.DataFrame(chart_rows)
            if chart_df.empty:
                st.caption("No data in this range yet.")
            else:
                # plot in market-local wall-clock (ET), naive, so the axis is readable
                # and consistent with the x-domain above
                chart_df["timestamp"] = (
                    chart_df["timestamp"].dt.tz_convert(market_tz).dt.tz_localize(None)
                )
                price_line = alt.Chart(chart_df).mark_line(color="#888888").encode(
                    x=alt.X("timestamp:T", scale=x_scale,
                            axis=alt.Axis(title="Time (ET)", format="%H:%M", tickCount=8)),
                    y=alt.Y("spot_price:Q", title="Price", scale=alt.Scale(zero=False)),
                )
                direction_points = alt.Chart(chart_df).mark_point(size=80, filled=True).encode(
                    x=alt.X("timestamp:T", scale=x_scale),
                    y="spot_price:Q",
                    color=alt.Color("direction:N", scale=alt.Scale(
                        domain=["bullish", "bearish", "neutral"],
                        range=["#2ecc71", "#e74c3c", "#95a5a6"],
                    ), legend=None),
                    tooltip=["timestamp:T", "direction:N", "spot_price:Q", "confidence:Q",
                             "composite_score:Q"],
                )
                # composite score on its own right-hand axis so signal swings and
                # actual price movement are visually comparable
                score_line = alt.Chart(chart_df).mark_line(
                    interpolate="step-after", color="#4a90d9", opacity=0.6, strokeWidth=1.5,
                ).encode(
                    x=alt.X("timestamp:T", scale=x_scale),
                    y=alt.Y("composite_score:Q", title="Signal score",
                            axis=alt.Axis(orient="right", titleColor="#4a90d9"),
                            scale=alt.Scale(domain=[-1, 1])),
                )
                # constant-datum encoding shares score_line's y scale without
                # fighting over the axis definition
                zero_rule = alt.Chart(chart_df).mark_rule(
                    color="#4a90d9", opacity=0.25, strokeDash=[4, 4],
                ).encode(y=alt.datum(0.0))

                # entry/exit markers for this ticker's trades, as vertical time rules
                # (the position stores option premium, a different scale than spot,
                # so a time rule is the honest way to mark it on the price chart)
                events = [
                    e for e in trade_events(
                        storage.get_open_positions(db_path),
                        storage.get_closed_positions(db_path),
                        ticker,
                    )
                    if cutoff is None or e["time"] >= cutoff
                ]
                price_layer = price_line + direction_points

                # pre-market key levels as horizontal reference lines - these
                # prior-day / overnight levels are the day's intraday support &
                # resistance. Shares the spot_price (left) y scale.
                today_iso = now_market.date().isoformat()
                day_setup_row = storage.get_day_setup(db_path, ticker, today_iso)
                if day_setup_row:
                    level_defs = [
                        ("Prior close", day_setup_row.get("prior_close"), "#f0a202"),
                        ("Prior high", day_setup_row.get("prior_high"), "#4a90d9"),
                        ("Prior low", day_setup_row.get("prior_low"), "#4a90d9"),
                        ("O/N high", day_setup_row.get("overnight_high"), "#9b59b6"),
                        ("O/N low", day_setup_row.get("overnight_low"), "#9b59b6"),
                    ]
                    level_rows = [
                        {"level": v, "label": name, "color": color}
                        for name, v, color in level_defs if isinstance(v, (int, float))
                    ]
                    if level_rows:
                        levels_df = pd.DataFrame(level_rows)
                        level_rules = alt.Chart(levels_df).mark_rule(
                            opacity=0.55, strokeDash=[6, 3], strokeWidth=1,
                        ).encode(
                            y=alt.Y("level:Q", scale=alt.Scale(zero=False)),
                            color=alt.Color("color:N", scale=None, legend=None),
                            tooltip=["label:N", "level:Q"],
                        )
                        price_layer = price_layer + level_rules

                if events:
                    # use the same x field name ("timestamp") as the other layers so
                    # the shared x axis renders its labels correctly
                    events_df = pd.DataFrame(events).rename(columns={"time": "timestamp"})
                    events_df["timestamp"] = (
                        events_df["timestamp"].dt.tz_convert(market_tz).dt.tz_localize(None)
                    )
                    trade_rules = alt.Chart(events_df).mark_rule(strokeWidth=1.5, opacity=0.7).encode(
                        x=alt.X("timestamp:T", scale=x_scale),
                        color=alt.Color("kind:N", scale=alt.Scale(
                            domain=["Entry", "Exit"], range=["#2ecc71", "#e74c3c"]),
                            legend=None),
                        tooltip=["kind:N", "label:N", "timestamp:T"],
                    )
                    price_layer = price_layer + trade_rules

                layered = alt.layer(
                    price_layer,
                    score_line + zero_rule,
                ).resolve_scale(y="independent", color="independent").properties(
                    height=340, padding={"left": 15, "right": 15, "top": 20, "bottom": 15},
                )
                # no .interactive() - a fixed time window shouldn't pan/zoom, and
                # zoom-binding the x scale suppresses the axis tick labels
                st.altair_chart(layered, use_container_width=True)
                st.caption("Dots = each signal (green bullish, red bearish, gray neutral) on the "
                           "price line. Blue stepped line = composite score (right axis). "
                           "Green/red vertical lines = trade entries/exits. Dashed horizontal "
                           "lines = pre-market key levels (prior close/high/low, overnight range).")

            overall_pct = accuracy.overall_accuracy_pct(evaluated)
            graded_count = sum(1 for s in evaluated if s["evaluated"])
            col_accuracy, col_daily = st.columns([1, 2])
            with col_accuracy:
                if overall_pct is None:
                    st.caption(f"Signals are graded {horizon_minutes:.0f} minutes after they're made - "
                                "not enough time has passed yet to grade any.")
                else:
                    st.metric(
                        f"Overall accuracy (graded {horizon_minutes:.0f} min later)",
                        f"{overall_pct:.0f}%",
                        help=f"Based on {graded_count} graded signals so far. Neutral ('no clear edge') "
                             "signals aren't graded since they made no directional call.",
                    )
            with col_daily:
                daily = accuracy.daily_accuracy_summary(evaluated, tz_name=config["market_hours"]["timezone"])
                if daily:
                    st.dataframe(
                        [
                            {"Date": d["date"], "Signals graded": d["total"], "Correct": d["hits"],
                             "Accuracy": f"{d['accuracy_pct']:.0f}%"}
                            for d in reversed(daily)
                        ],
                        hide_index=True,
                        use_container_width=True,
                    )

            with st.expander("Calibration, per-signal accuracy & weight tuning", expanded=False):
                # --- Automatic self-calibration -----------------------------
                st.subheader("Automatic self-calibration")
                cal_on = st.toggle(
                    "Auto-calibrate daily",
                    value=storage.get_calibration_enabled(db_path),
                    key="calibration_toggle",
                    help="After market close each day the worker nudges each ticker's weights "
                         "toward its accuracy-based suggestion (gradually), auto-inverts reliably-"
                         "wrong signals, and remaps raw confidence to observed accuracy. Everything "
                         "below is audited and fully revertible.",
                )
                if cal_on != storage.get_calibration_enabled(db_path):
                    storage.set_calibration_enabled(db_path, cal_on)
                    st.rerun()

                cal_cfg = config.get("calibration", {})
                last_run = storage.get_last_calibration_date(db_path) or "never"
                active_inv = storage.get_inversions(db_path, ticker)
                inv_names = (", ".join(CATEGORY_INFO.get(c, (c, ""))[0] for c in active_inv)
                             if active_inv else "none")
                st.caption(
                    f"Last run: **{last_run}** · learning rate "
                    f"{cal_cfg.get('learning_rate', 0.25):.0%}/day · "
                    f"auto-inverted for {ticker}: {inv_names}"
                )

                events = storage.get_calibration_events(db_path, limit=15)
                if events:
                    _EVENT_LABELS = {
                        "weights_nudged": "weights nudged", "inversion_added": "inverted",
                        "inversion_removed": "un-inverted",
                        "confidence_map_updated": "confidence remapped", "reverted": "reverted",
                    }
                    def _event_detail(kind, detail):
                        if kind in ("inversion_added", "inversion_removed"):
                            cat = detail.get("category", "")
                            name = CATEGORY_INFO.get(cat, (cat, ""))[0]
                            acc = detail.get("accuracy_pct")
                            return f"{name}" + (f" ({acc:.0f}% acc)" if acc is not None else "")
                        if kind == "confidence_map_updated":
                            return f"{len(detail.get('bands', []))} band(s)"
                        return ""
                    st.dataframe(
                        [
                            {
                                "When": datetime.fromisoformat(ev["created_at"]).strftime("%m-%d %H:%M"),
                                "Ticker": ev["ticker"],
                                "Action": _EVENT_LABELS.get(ev["kind"], ev["kind"]),
                                "Detail": _event_detail(ev["kind"], json.loads(ev["detail_json"])),
                            }
                            for ev in events
                        ],
                        hide_index=True, use_container_width=True,
                    )
                else:
                    st.caption("No calibration adjustments yet.")

                if st.button("Revert all auto-calibration", key="revert_all_calibration",
                             help="Clears every auto-applied weight override, per-ticker inversion, "
                                  "and confidence map across all tickers. Manual config inversions "
                                  "and manually-applied weights are restored to config defaults too."):
                    for t in config["tickers"]:
                        storage.clear_weight_overrides(db_path, t)
                        for cat in storage.get_inversions(db_path, t):
                            storage.remove_inversion(db_path, t, cat)
                        storage.clear_confidence_bands(db_path, t)
                        storage.log_calibration_event(db_path, t, "reverted", {})
                    st.toast("Auto-calibration reverted for all tickers.")
                    st.rerun()

                st.divider()

                if calibration:
                    st.subheader("Confidence calibration")
                    st.caption("When the app predicts a confidence level, how often is it actually "
                               "right? Well-calibrated predictions have observed accuracy close to "
                               "the predicted band.")
                    st.dataframe(
                        [
                            {"Predicted confidence": band["band"],
                             "Signals graded": band["count"],
                             "Observed accuracy": f"{band['observed_accuracy_pct']:.0f}%"}
                            for band in calibration
                        ],
                        hide_index=True,
                        use_container_width=True,
                    )

                graded_categories = {k: v for k, v in category_results.items() if v["graded_count"] > 0}
                if graded_categories:
                    st.subheader("Per-signal accuracy")
                    st.caption("Which individual signals are actually calling direction correctly, "
                               "versus just riding the composite score.")
                    rows = sorted(graded_categories.items(), key=lambda kv: kv[1]["accuracy_pct"], reverse=True)
                    st.dataframe(
                        [
                            {
                                "Signal": CATEGORY_INFO.get(cat, (cat, ""))[0],
                                "Weight": f"{active_weights.get(cat, 0) * 100:.0f}%",
                                "Signals graded": result["graded_count"],
                                "Accuracy": f"{result['accuracy_pct']:.0f}%",
                            }
                            for cat, result in rows
                        ],
                        hide_index=True,
                        use_container_width=True,
                    )

                    # --- accuracy by market context -------------------------
                    tz_name = config["market_hours"].get("timezone", "America/New_York")
                    tod = accuracy.bucket_evaluated(
                        evaluated, lambda s: accuracy.context_time_of_day(s, tz_name))
                    vol = accuracy.bucket_evaluated(evaluated, accuracy.context_volatility_regime)
                    streaks = accuracy.bucket_evaluated(
                        evaluated, accuracy.context_direction_streak)
                    if any(b["graded"] for b in tod.values()) or any(b["graded"] for b in vol.values()):
                        st.subheader("Accuracy by market context")
                        st.caption("The same graded calls, split by *when* and *what kind of day* - "
                                   "which is more honest than one blended number. Read-only for now.")

                        def _ctx_rows(buckets, order):
                            return [
                                {
                                    "Context": label,
                                    "Signals graded": buckets[label]["graded"],
                                    "Accuracy": (f"{buckets[label]['accuracy_pct']:.0f}%"
                                                 if buckets[label]["accuracy_pct"] is not None else "-"),
                                }
                                for label in order if label in buckets and buckets[label]["graded"]
                            ]

                        tod_rows = _ctx_rows(tod, ["morning", "midday", "afternoon"])
                        vol_rows = _ctx_rows(vol, ["calm", "stressed"])
                        streak_rows = _ctx_rows(
                            streaks, ["fresh (1-3)", "building (4-15)", "sustained (16+)"])
                        ctx_a, ctx_b, ctx_c = st.columns(3)
                        with ctx_a:
                            st.markdown("**By time of day** (ET)")
                            if tod_rows:
                                st.dataframe(tod_rows, hide_index=True, use_container_width=True)
                            else:
                                st.caption("Not enough graded calls yet.")
                        with ctx_b:
                            st.markdown("**By volatility regime**")
                            if vol_rows:
                                st.dataframe(vol_rows, hide_index=True, use_container_width=True)
                            else:
                                st.caption("Not enough graded calls yet.")
                        with ctx_c:
                            st.markdown("**By signal persistence**")
                            if streak_rows:
                                st.dataframe(streak_rows, hide_index=True, use_container_width=True)
                            else:
                                st.caption("Not enough graded calls yet.")

                    candidates = accuracy.inversion_candidates(
                        category_results, min_graded=config.get("weight_suggestion_min_graded", 10),
                    )
                    if candidates:
                        names = ", ".join(CATEGORY_INFO.get(c, (c, ""))[0] for c in candidates)
                        st.info(
                            f"**Consider inverting:** {names} - reliably wrong over enough graded "
                            "signals that the *opposite* of its call has been the better bet. To act "
                            "on this, add the category to `invert_categories` in config/settings.yaml.",
                            icon=":material/swap_vert:",
                        )

                    suggested = accuracy.suggest_weights(
                        category_results, active_weights,
                        min_graded=config.get("weight_suggestion_min_graded", 10),
                    )
                    if suggested is not None:
                        st.markdown(f"**Suggested weight rebalancing for {ticker}** (based on "
                                    "its graded history)")
                        st.caption(
                            f"Applies to **{ticker} only** - each ticker keeps its own weights. "
                            "Nothing is applied automatically. Categories with fewer than "
                            f"{config.get('weight_suggestion_min_graded', 10)} graded signals keep "
                            "their current weight untouched; the rest are redistributed proportional "
                            "to how far above a coin-flip (50%) their accuracy has been."
                        )
                        st.dataframe(
                            [
                                {
                                    "Signal": CATEGORY_INFO.get(cat, (cat, ""))[0],
                                    "Current weight": f"{active_weights.get(cat, 0) * 100:.0f}%",
                                    "Suggested weight": f"{suggested.get(cat, 0) * 100:.0f}%",
                                }
                                for cat in active_weights
                            ],
                            hide_index=True,
                            use_container_width=True,
                        )

                        if weight_override is not None:
                            applied_at = datetime.fromisoformat(weight_override[1])
                            st.caption(f"{ticker} weights: **applied suggestion** from "
                                       f"{applied_at.strftime('%Y-%m-%d %H:%M')} UTC. The worker "
                                       "picks up changes on its next cycle.")
                        else:
                            st.caption(f"{ticker} weights: **config defaults** (settings.yaml).")

                        col_apply, col_revert = st.columns(2)
                        with col_apply:
                            if st.button(f"Apply to {ticker}", key="apply_weights",
                                         use_container_width=True):
                                storage.set_weight_overrides(db_path, ticker, suggested)
                                st.rerun()
                        with col_revert:
                            if weight_override is not None and st.button(
                                    f"Revert {ticker} to defaults", key="revert_weights",
                                    use_container_width=True):
                                storage.clear_weight_overrides(db_path, ticker)
                                st.rerun()

        render_accuracy_panel()

    # --- Strategy lab: shadow strategies' track records -----------------
    with st.container(key="strategy_lab_card"):
        st.header(":material/science: Strategy lab")

        @st.fragment(run_every="60s")
        def render_strategy_lab() -> None:
            strategies = config.get("shadow_strategies", [])
            if not strategies:
                st.caption("No shadow strategies configured (settings.yaml -> shadow_strategies).")
                return
            st.caption(
                "Virtual strategies trading a paper-within-paper book on every ticker, "
                "every cycle - same signals, honest bid/ask fills, zero wallet impact. "
                "**Verdict** tests whether average P&L per trade is distinguishable "
                "from zero (|t| >= 2), *not* whether the win rate beats 50% - "
                "asymmetric exits move the natural win rate on their own. "
                "**Need** is how many trades it would take to prove an effect the "
                "size currently observed. With ~10 strategies running, expect one to "
                "clear the bar by luck roughly half the time: treat a single winner "
                "as a hypothesis to re-test, not a result."
            )
            closed = storage.get_closed_shadow_positions(db_path)
            open_rows = storage.get_open_shadow_positions(db_path)
            open_counts: dict[str, int] = {}
            for row in open_rows:
                open_counts[row["strategy"]] = open_counts.get(row["strategy"], 0) + 1

            cards = strategy_scorecard(closed)
            MIN_TRADES = 20
            edges = strategy_edge(closed, min_trades=MIN_TRADES)
            table = []
            for strat in strategies:
                name = strat.get("name", "?")
                card = cards.get(name)
                if card is None:
                    table.append({"Strategy": name, "Trades": 0, "Win rate": "-",
                                  "Total P&L": "-", "Avg P&L": "-", "Profit factor": "-",
                                  "Max DD": "-", "Open": open_counts.get(name, 0),
                                  "t": "-", "Need": "-", "Verdict": "no trades yet"})
                    continue
                edge = edges.get(name, {})
                need = edge.get("trades_needed")
                table.append({
                    "Strategy": name,
                    "Trades": card["trades"],
                    "Win rate": f"{card['win_rate_pct']:.0f}%" if card["win_rate_pct"] is not None else "-",
                    "Total P&L": f"${card['total_pnl']:+,.0f}",
                    "Avg P&L": f"${card['avg_pnl']:+,.0f}" if card["avg_pnl"] is not None else "-",
                    "Profit factor": f"{card['profit_factor']:.2f}" if card["profit_factor"] is not None else "-",
                    "Max DD": f"${card['max_drawdown']:,.0f}",
                    "Open": open_counts.get(name, 0),
                    "t": f"{edge.get('t_stat', 0):+.2f}",
                    "Need": f"{need:,}" if need else "-",
                    "Verdict": edge.get("verdict", "-"),
                })
            # strongest first among those with data; warming-up rows keep config order
            table.sort(key=lambda r: (r["Trades"] == 0,
                                      -(cards.get(r["Strategy"], {}).get("total_pnl") or 0.0)))
            st.dataframe(table, hide_index=True, use_container_width=True)

        render_strategy_lab()

with right_col:
    # --- Wallet card ---------------------------------------------------
    with st.container(key="wallet_card"):
        top_account, top_settings = st.columns([3, 1])
        with top_account:
            st.subheader(":material/account_balance_wallet: Wallet")
        with top_settings:
            with st.popover(":material/settings:"):
                st.markdown("**Balance**")
                balance = storage.get_balance(db_path)
                if st.button("Reset to starting balance", key="reset_balance"):
                    storage.set_balance(db_path, config["account"]["starting_balance"])
                    st.rerun()
                manual_balance = st.number_input(
                    "Set balance to ($)", min_value=0.0, value=balance, step=100.0, key="manual_balance_input",
                )
                if st.button("Set balance", key="set_balance"):
                    storage.set_balance(db_path, manual_balance)
                    st.rerun()

                st.divider()

                st.markdown("**Trade history**")
                st.caption("Permanently deletes all closed trade records. Open positions and "
                           "the account balance are not affected.")
                if st.button("Clear trade history", key="clear_trade_history"):
                    storage.clear_closed_positions(db_path)
                    st.rerun()

        @st.fragment(run_every="15s")
        def render_wallet_metrics() -> None:
            balance = storage.get_balance(db_path)
            st.metric("Virtual account balance", f"${balance:,.2f}")

            pnl_summary = summarize_pnl(
                storage.get_open_positions(db_path), storage.get_closed_positions(db_path),
                tz_name=config["market_hours"].get("timezone", "America/New_York"),
            )

            def _pnl_metric(column, label: str, value: float) -> None:
                # delta is a signed string purely for the green/red arrow; None hides it at zero
                column.metric(label, f"${value:,.0f}", delta=f"{value:+,.2f}" if value else None)

            col_today, col_total, col_unrealized = st.columns(3)
            _pnl_metric(col_today, "Realized today", pnl_summary["realized_today"])
            _pnl_metric(col_total, "Realized total", pnl_summary["realized_total"])
            _pnl_metric(col_unrealized, "Open unrealized", pnl_summary["unrealized_open"])

        render_wallet_metrics()

    # --- Daily P&L calendar card ----------------------------------------
    with st.container(key="calendar_card"):
        st.subheader(":material/calendar_month: Daily P&L")
        with st.expander("Show calendar", expanded=False):
            # Fragment lives inside the expander so its open/closed state is owned
            # outside the auto-rerun, and a collapsed calendar doesn't run its timer.
            @st.fragment(run_every="60s")
            def render_calendar() -> None:
                market_tz = ZoneInfo(config["market_hours"].get("timezone", "America/New_York"))
                today = datetime.now(market_tz).date()
                if "calendar_anchor" not in st.session_state:
                    st.session_state.calendar_anchor = (today.year, today.month)

                prev_col, title_col, next_col = st.columns([1, 4, 1])
                if prev_col.button("‹", key="cal_prev", use_container_width=True):
                    st.session_state.calendar_anchor = shift_month(*st.session_state.calendar_anchor, -1)
                if next_col.button("›", key="cal_next", use_container_width=True):
                    st.session_state.calendar_anchor = shift_month(*st.session_state.calendar_anchor, 1)
                year, month = st.session_state.calendar_anchor
                title_col.markdown(
                    f"<div style='text-align:center; font-weight:600; padding-top:0.35rem;'>"
                    f"{_cal.month_name[month]} {year}</div>",
                    unsafe_allow_html=True,
                )

                pnl_by_day = daily_realized_pnl(
                    storage.get_closed_positions(db_path),
                    tz_name=config["market_hours"].get("timezone", "America/New_York"),
                )
                weeks = month_calendar_cells(pnl_by_day, year, month)

                head = "".join(
                    f"<th style='padding:4px; font-size:0.72rem; opacity:0.6; font-weight:600;'>{d}</th>"
                    for d in ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
                )
                body_rows = []
                for week in weeks:
                    cells = []
                    for cell in week:
                        pnl = cell["pnl"]
                        if not cell["in_month"]:
                            bg, opacity = "transparent", "0.28"
                        elif pnl is None:
                            bg, opacity = "transparent", "1"
                        elif pnl >= 0:
                            bg, opacity = "rgba(46,204,113,0.18)", "1"
                        else:
                            bg, opacity = "rgba(231,76,60,0.18)", "1"
                        outline = ("box-shadow: inset 0 0 0 2px #4a90d9;"
                                   if cell["date"] == today else "")
                        amount = ""
                        if cell["in_month"] and pnl is not None:
                            amount = (f"<div style='font-size:0.72rem; font-weight:600;'>"
                                      f"{'+' if pnl >= 0 else '-'}${abs(pnl):,.0f}</div>")
                        cells.append(
                            f"<td style='height:52px; width:14.28%; vertical-align:top; "
                            f"padding:3px; border-radius:6px; background:{bg}; opacity:{opacity}; "
                            f"{outline} color:inherit;'>"
                            f"<div style='font-size:0.72rem; opacity:0.7;'>{cell['day']}</div>"
                            f"{amount}</td>"
                        )
                    body_rows.append(f"<tr>{''.join(cells)}</tr>")

                st.markdown(
                    f"<table style='width:100%; border-collapse:separate; border-spacing:3px; "
                    f"table-layout:fixed;'><thead><tr>{head}</tr></thead>"
                    f"<tbody>{''.join(body_rows)}</tbody></table>",
                    unsafe_allow_html=True,
                )

            render_calendar()

    # --- Place order card -----------------------------------------------
    with st.container(key="order_card"):
        st.subheader(":material/shopping_cart: Place a paper trade")

        # --- Auto-pilot mode: worker-side automated entries. Manual trading
        # below always stays available regardless of this control. ------------
        _AP_MODE_LABELS = {"off": "Off", "day": "Day session", "continuous": "Continuous"}
        _AP_LABEL_MODES = {label: mode for mode, label in _AP_MODE_LABELS.items()}
        ap_mode, ap_armed_date = storage.get_autopilot_state(db_path)
        market_tz_ap = ZoneInfo(config["market_hours"].get("timezone", "America/New_York"))
        now_ap = datetime.now(market_tz_ap)
        today_ap = now_ap.date()

        # sync widget <- DB when the DB changed outside this widget (worker's
        # day-session auto-disarm, another browser tab) so a stale widget value
        # never re-arms a session the worker just ended
        db_label = _AP_MODE_LABELS[ap_mode]
        if st.session_state.get("autopilot_mode_synced") != db_label:
            st.session_state["autopilot_mode_control"] = db_label
            st.session_state["autopilot_mode_synced"] = db_label

        selected_label = st.segmented_control(
            "Auto-pilot",
            list(_AP_MODE_LABELS.values()),
            key="autopilot_mode_control",
            help="Day session: arms the worker for one trading day - it waits for the "
                 "opening range, makes ONE decision (the strongest qualifying signal "
                 "across all tickers), manages the exit, force-closes the auto position "
                 "before the bell, and disarms itself afterwards. Continuous: the worker "
                 "may enter any time guard rails pass. Manual trading below keeps "
                 "working either way.",
        )
        # clicking the selected segment deselects it -> read that as Off
        selected_mode = _AP_LABEL_MODES.get(selected_label, "off")
        if selected_label != st.session_state["autopilot_mode_synced"]:
            st.session_state["autopilot_mode_synced"] = _AP_MODE_LABELS[selected_mode]
            if selected_mode == "day":
                # arm for today if the session can still trade, else the next trading day
                if (is_market_open(config) or
                        (today_ap.weekday() < 5
                         and today_ap.isoformat() not in config.get("market_holidays", [])
                         and minutes_to_market_close(config) > 0)):
                    armed = today_ap
                else:
                    armed = next_trading_day(config, today_ap)
                storage.set_autopilot_state(db_path, "day", armed.isoformat())
            else:
                storage.set_autopilot_state(db_path, selected_mode)
            ap_mode, ap_armed_date = storage.get_autopilot_state(db_path)

        ap_cfg = config.get("autopilot", {})
        if ap_mode == "day" and ap_armed_date:
            tactic = ap_cfg.get("tactic", "opening_range").replace("_", " ")
            open_hm = config["market_hours"].get("open", "09:30")
            open_h, open_m = (int(p) for p in open_hm.split(":"))
            open_minutes = open_h * 60 + open_m
            d_start = open_minutes + ap_cfg.get("decision_start_minutes", 30)
            d_end = open_minutes + ap_cfg.get("decision_end_minutes", 90)
            st.caption(f":material/event_available: Armed for **{ap_armed_date}** · "
                       f"tactic: {tactic} · decides between "
                       f"{d_start // 60:02d}:{d_start % 60:02d}-{d_end // 60:02d}:{d_end % 60:02d} ET · "
                       f"{ap_cfg.get('max_entries_per_session', 1)} trade max · "
                       f"ends flat before the bell · disarms after the session")
        if ap_mode != "off":
            def _today_ap(iso_ts):
                return bool(iso_ts) and datetime.fromisoformat(iso_ts).astimezone(market_tz_ap).date() == today_ap

            all_rows = list(storage.get_open_positions(db_path)) + list(storage.get_closed_positions(db_path))
            auto_today = sum(1 for r in all_rows if r["opened_by"] == "auto" and _today_ap(r["entry_time"]))
            auto_pnl_today = sum((r["pnl"] or 0.0) for r in storage.get_closed_positions(db_path)
                                 if r["opened_by"] == "auto" and _today_ap(r["exit_time"]))
            limit = ap_cfg.get("daily_loss_limit_pct", 10) / 100.0 * config["account"]["starting_balance"]
            breaker = " · :material/block: circuit breaker TRIPPED" if auto_pnl_today <= -limit else ""
            trades_cap = (ap_cfg.get("max_entries_per_session", 1)
                          if ap_mode == "day" and ap_cfg.get("tactic", "opening_range") == "opening_range"
                          else ap_cfg.get("max_trades_per_day", 4))
            st.caption(f":material/smart_toy: Auto-pilot {_AP_MODE_LABELS[ap_mode].upper()} · "
                       f"trades today {auto_today}/{trades_cap} · "
                       f"auto P&L today ${auto_pnl_today:+,.0f}{breaker} · "
                       f"enters on ≥{ap_cfg.get('min_confidence_pct', 55)}% confidence, "
                       f"target +{ap_cfg.get('profit_target_pct', 50)}% / "
                       f"stop {ap_cfg.get('stop_loss_pct', -35)}%")

        # read fresh here (the wallet balance now lives in its own fragment scope)
        balance = storage.get_balance(db_path)
        default_amount = round(balance * config["account"]["risk_per_trade_pct"] / 100.0, 2)
        if "trade_amount" not in st.session_state:
            st.session_state.trade_amount = default_amount

        dollar_amount = st.number_input(
            "Amount to risk ($)", min_value=0.0, step=50.0, key="trade_amount",
        )

        def _set_amount(value: float) -> None:
            # on_click callbacks run before widgets re-instantiate, so writing the
            # widget's session-state key here is safe
            st.session_state.trade_amount = round(value, 2)

        quick_cols = st.columns(5)
        quick_cols[0].button("$100", key="amt_100", use_container_width=True,
                             on_click=_set_amount, args=(100.0,))
        quick_cols[1].button("$200", key="amt_200", use_container_width=True,
                             on_click=_set_amount, args=(200.0,))
        quick_cols[2].button("25%", key="amt_25pct", use_container_width=True,
                             on_click=_set_amount, args=(balance * 0.25,))
        quick_cols[3].button("50%", key="amt_50pct", use_container_width=True,
                             on_click=_set_amount, args=(balance * 0.50,))
        quick_cols[4].button("Max", key="amt_max", use_container_width=True,
                             on_click=_set_amount, args=(balance,))

        def place_trade(option_type: str, composite_score: float) -> None:
            chain = market_data.get_option_chain(ticker)
            if chain is None:
                st.error("Couldn't fetch a live quote right now - try again in a moment.")
                return
            df = chain.calls if option_type == "call" else chain.puts
            contract = market_data.find_atm_contract(df, chain.spot)
            if contract is None:
                st.error("No option contract available for this ticker right now.")
                return
            entry_price = market_data.contract_entry_price(contract)  # honest fill: ask-side
            if entry_price is None:
                st.error("No usable quote on that contract right now.")
                return
            # read via session state, not the closure, so a fragment-only rerun
            # can't act on a stale amount
            amount = st.session_state.get("trade_amount", 0.0)
            contracts = calculate_contracts(amount, 100.0, entry_price)
            if contracts <= 0:
                st.warning("That amount isn't enough for even one contract at the current price.")
                return
            position_id = buy_position(
                db_path, ticker, option_type, float(contract["strike"]),
                chain.expiration, entry_price, contracts, composite_score,
            )
            if position_id:
                st.success(f"Bought {contracts} {ticker} {contract['strike']} {option_type}"
                           f" @ ${entry_price:.2f}")
                st.rerun(scope="app")  # full-page refresh: balance and positions changed
            else:
                st.error("Trade rejected - insufficient balance.")

        @st.fragment(run_every="30s")
        def render_trade_buttons() -> None:
            """Re-reads the latest signal on the same cadence as the banner, so the
            suggested-side border can't contradict a freshly flipped signal."""
            snap = storage.get_latest_signal(db_path, ticker)
            suggested_type = None
            if snap is not None:
                if snap["direction"] == "bullish":
                    suggested_type = "call"
                elif snap["direction"] == "bearish":
                    suggested_type = "put"
            composite_score = snap["composite_score"] if snap is not None else 0.0

            call_key = "buy_call_suggested" if suggested_type == "call" else "buy_call_plain"
            put_key = "buy_put_suggested" if suggested_type == "put" else "buy_put_plain"

            col_call, col_put = st.columns(2)
            with col_call:
                if st.button("Buy 0DTE calls", key=call_key, use_container_width=True):
                    place_trade("call", composite_score)
            with col_put:
                if st.button("Buy 0DTE puts", key=put_key, use_container_width=True):
                    place_trade("put", composite_score)

        render_trade_buttons()

    # --- Open positions card ---------------------------------------------
    with st.container(key="positions_card"):
        st.subheader(":material/list_alt: Open positions")
        st.caption("Setting a Profit target or Stop loss auto-closes that trade when it's "
                   "hit (checked each worker cycle).")

        @st.fragment(run_every="15s")
        def render_open_positions() -> None:
            """Refetches a live quote for each held contract directly - independent
            of the worker's poll cadence - so unresolved positions' P&L updates
            without needing to touch the page."""
            open_positions = storage.get_open_positions(db_path)
            if not open_positions:
                st.caption("No open positions.")
                return

            chains_by_ticker = {}
            for pos in open_positions:
                if pos["ticker"] not in chains_by_ticker:
                    chains_by_ticker[pos["ticker"]] = market_data.get_option_chain(pos["ticker"])

            for pos in open_positions:
                chain = chains_by_ticker.get(pos["ticker"])
                live_price = market_data.find_contract_price(chain, pos["option_type"], pos["strike"])
                current_price = live_price if live_price is not None else pos["current_price"]
                if live_price is not None:
                    storage.update_position_price(db_path, pos["id"], live_price)

                # fast per-trade auto-close: on the dashboard's ~15s cadence, don't
                # wait up to a minute for the worker. Same rule (price_target_exit),
                # and close_position is race-safe so a simultaneous worker close
                # can't double-credit.
                if live_price is not None:
                    reason = price_target_exit(Position.from_row(pos), live_price)
                    if reason is not None:
                        if close_position(db_path, pos["id"], live_price, reason) is not None:
                            st.toast(f"Auto-closed {pos['ticker']} {pos['option_type']} "
                                     f"({reason.replace('_', ' ')})", icon=":material/gavel:")
                            st.rerun(scope="app")

                pnl_pct = None
                if current_price is not None:
                    _, pnl_pct = calculate_pnl(pos["entry_price"], current_price, pos["contracts"])
                if pnl_pct is None:
                    card_key = f"position_flat_{pos['id']}"
                elif pnl_pct >= 0:
                    card_key = f"position_win_{pos['id']}"
                else:
                    card_key = f"position_loss_{pos['id']}"

                with st.container(key=card_key, border=True):
                    opened_tag = " · :material/smart_toy: auto" if pos["opened_by"] == "auto" else ""
                    st.write(f"**{pos['ticker']} {pos['strike']} {pos['option_type']}** "
                              f"x{pos['contracts']} @ ${pos['entry_price']:.2f} "
                              f"(exp {pos['expiration']}){opened_tag}")
                    if current_price is not None:
                        pnl_dollars, pnl_pct = calculate_pnl(pos["entry_price"], current_price, pos["contracts"])
                        st.write(f"Current: ${current_price:.2f} | "
                                  f"Unrealized P&L: ${pnl_dollars:,.2f} ({pnl_pct:+.1f}%)")
                    # peak gain since entry (high-water mark that arms the trailing stop)
                    peak = pos["max_price"]
                    if peak is not None and pos["entry_price"] > 0 and peak > pos["entry_price"]:
                        peak_pct = (peak - pos["entry_price"]) / pos["entry_price"] * 100.0
                        st.caption(f":material/arrow_upward: peak +{peak_pct:.0f}% "
                                   f"(${peak:.2f}) since entry")
                    # spread readout: the round-trip cost honest fills bake in
                    # (prices are bid-side, entries fill ask-side)
                    row = market_data.find_contract_row(chain, pos["option_type"], pos["strike"])
                    spread = market_data.contract_spread_pct(row) if row is not None else None
                    if spread is not None:
                        st.caption(f":material/swap_horiz: bid/ask spread {spread:.1f}% "
                                   "(valued at bid)")
                    # per-trade auto-exit targets (None = off). Profit stored
                    # positive, stop stored negative; both shown as magnitudes.
                    stored_pt = pos["profit_target_pct"]
                    stored_sl = pos["stop_loss_pct"]
                    stored_sl_mag = abs(stored_sl) if stored_sl is not None else None
                    pt_col, sl_col = st.columns(2)
                    sel_pt = pt_col.selectbox(
                        "Profit target", EXIT_PCT_OPTIONS,
                        index=EXIT_PCT_OPTIONS.index(stored_pt) if stored_pt in EXIT_PCT_OPTIONS else 0,
                        format_func=lambda v: "None" if v is None else f"+{v}%",
                        key=f"pt_{pos['id']}",
                    )
                    sel_sl = sl_col.selectbox(
                        "Stop loss", EXIT_PCT_OPTIONS,
                        index=EXIT_PCT_OPTIONS.index(stored_sl_mag) if stored_sl_mag in EXIT_PCT_OPTIONS else 0,
                        format_func=lambda v: "None" if v is None else f"-{v}%",
                        key=f"sl_{pos['id']}",
                    )
                    new_sl = None if sel_sl is None else -sel_sl
                    if sel_pt != stored_pt or new_sl != stored_sl:
                        storage.set_position_exit_targets(db_path, pos["id"], sel_pt, new_sl)

                    if pos["suggested_exit_reason"]:
                        st.warning(f"SUGGESTED EXIT: {pos['suggested_exit_reason']}")
                    if st.button("Close position", key=f"close_{pos['id']}"):
                        exit_price = current_price if current_price is not None else pos["entry_price"]
                        reason = pos["suggested_exit_reason"] or "manual"
                        close_position(db_path, pos["id"], exit_price, reason)
                        st.rerun(scope="app")  # balance and P&L metrics outside this fragment changed

            st.caption(f"Refreshes automatically every 15s &middot; updated {datetime.now().strftime('%H:%M:%S')}")

        render_open_positions()

    # --- Trade history card ------------------------------------------------
    with st.container(key="history_card"):
        st.subheader(":material/history: Trade history")
        with st.expander("Show history", expanded=False):
            @st.fragment(run_every="60s")
            def render_trade_history() -> None:
                closed_positions = storage.get_closed_positions(db_path)
                if not closed_positions:
                    st.caption("No closed trades yet.")
                    return
                st.dataframe([
                    {
                        "ticker": p["ticker"], "type": p["option_type"], "strike": p["strike"],
                        "entry": p["entry_price"], "exit": p["exit_price"], "pnl": p["pnl"],
                        "reason": p["exit_reason"], "by": p["opened_by"], "closed": p["exit_time"],
                    }
                    for p in closed_positions
                ], hide_index=True, use_container_width=True)

            render_trade_history()
