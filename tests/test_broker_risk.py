import pytest

from broker.models import OrderRequest, validate_shape
from broker.risk import AccountState, MarketState, effective_limits, check

TODAY = "2026-11-30"
OPEN = MarketState(open=True, today=TODAY, minutes_to_close=200)
LIMITS = effective_limits({})


def acct(**kw):
    base = dict(equity=5000, cash=5000, peak_equity=5000, start_equity=5000)
    base.update(kw)
    return AccountState(**base)


def stock(side="buy", qty=5, **kw):
    return OrderRequest("a", "TSLA", side, qty, **kw)


def opt(side="buy", qty=1, exp=TODAY, limit=2.0, **kw):
    return OrderRequest("a", "QQQ", side, qty, asset="option", option_type="call", strike=600,
                        expiration=exp, limit_price=limit, **kw)


def rule(*a, **k):
    r = check(*a, **k)
    return r.rule if r else None


def test_shape_validation():
    assert validate_shape(stock(qty=0)) == "qty must be a positive whole number"
    assert validate_shape(OrderRequest("a", "QQQ", "buy", 1, asset="option")) == "option_type must be call or put"
    assert validate_shape(OrderRequest("a", "TSLA", "short", 1)) == "side must be buy or sell"
    assert validate_shape(opt()) is None
    assert opt().instrument == "QQQ 2026-11-30 600C"


def test_plain_buy_passes():
    assert rule(stock(), 100.0, 0.0, acct(), OPEN, LIMITS) is None


def test_market_closed_blocks_everything():
    closed = MarketState(open=False, today=TODAY, minutes_to_close=0)
    assert rule(stock(), 100.0, 0, acct(), closed, LIMITS) == "market_closed"
    assert rule(stock("sell"), 100.0, 0, acct(held={"TSLA": {"qty": 5}}), closed, LIMITS) == "market_closed"


def test_position_size_cash_and_count():
    assert rule(stock(qty=11), 100.0, 0, acct(), OPEN, LIMITS) == "max_position_size"   # 1100 > 20% of 5000
    assert rule(stock(qty=5), 100.0, 0, acct(cash=400), OPEN, LIMITS) == "insufficient_cash"
    held = {f"T{i}": {"qty": 1, "price": 1} for i in range(8)}
    assert rule(stock(), 100.0, 0, acct(held=held), OPEN, LIMITS) == "max_open_positions"
    held["TSLA"] = held.pop("T0") | {"qty": 2, "price": 100}
    assert rule(stock(), 100.0, 0, acct(held=held), OPEN, LIMITS) is None      # adding to a held name
    held["TSLA"]["qty"] = 6
    assert rule(stock(), 100.0, 0, acct(held=held), OPEN, LIMITS) == "max_position_size"   # 600 + 500


def test_loss_limits_block_buys_but_not_sells():
    down_today = acct(equity=4650, start_equity=5000, peak_equity=5000)        # -7%
    assert rule(stock(qty=1), 100.0, 0, down_today, OPEN, LIMITS) == "daily_loss_limit"
    drawn = acct(equity=3400, start_equity=3400, peak_equity=5000)             # -32% from peak
    assert rule(stock(qty=1), 100.0, 0, drawn, OPEN, LIMITS) == "max_drawdown"
    drawn.held = {"TSLA": {"qty": 5}}
    assert rule(stock("sell"), 100.0, 0, drawn, OPEN, LIMITS) is None
    assert rule(stock(qty=1), 100.0, 0, acct(paused=True), OPEN, LIMITS) == "paused"


def test_cannot_sell_more_than_held():
    assert rule(stock("sell", qty=6), 100.0, 0, acct(held={"TSLA": {"qty": 5}}), OPEN, LIMITS) == "not_held"
    assert rule(stock("sell"), 100.0, 0, acct(), OPEN, LIMITS) == "not_held"


def test_option_rules():
    assert rule(opt(limit=None), 2.0, 1, acct(), OPEN, LIMITS) == "limit_required"
    assert rule(opt(limit=1.9), 2.0, 1, acct(), OPEN, LIMITS) == "limit_not_reached"
    assert rule(opt(exp="2026-11-27"), 2.0, 1, acct(), OPEN, LIMITS) == "expired"
    assert rule(opt(qty=3, limit=2.0), 2.0, 1, acct(), OPEN, LIMITS) == "max_0dte_premium"   # 600 > 500
    assert rule(opt(qty=3, exp="2026-12-18", limit=2.0), 2.0, 1, acct(), OPEN, LIMITS) is None
    late = MarketState(open=True, today=TODAY, minutes_to_close=20)
    assert rule(opt(), 2.0, 1, acct(), late, LIMITS) == "late_0dte"
    fed = MarketState(open=True, today=TODAY, minutes_to_close=200, catalyst_imminent=True)
    assert rule(opt(), 2.0, 1, acct(), fed, LIMITS) == "catalyst"
    assert rule(opt(exp="2026-12-18"), 2.0, 1, acct(), fed, LIMITS) is None


def test_agent_can_only_tighten():
    lim = effective_limits({"risk": {"max_position_pct": 15}},
                           {"risk": {"max_position_pct": 50, "max_open_positions": 3,
                                     "zero_dte_cutoff_minutes": 10, "allowed_assets": ["stock", "crypto"]}})
    assert lim["max_position_pct"] == 15            # agent asked for more: ignored
    assert lim["max_open_positions"] == 3           # agent asked for less: applied
    assert lim["zero_dte_cutoff_minutes"] == 30     # shorter cutoff is looser: ignored
    assert lim["allowed_assets"] == ["stock"]
    assert rule(opt(exp="2026-12-18"), 2.0, 1, acct(), OPEN, lim) == "asset_not_allowed"
