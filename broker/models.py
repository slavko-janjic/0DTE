"""What an agent asks for, and how an instrument is named.

An order is long-only by construction: 'buy' opens or adds, 'sell' only
reduces a position the agent holds. There is no way to express a short sale or
a naked short option - the risk gate never has to catch one.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

OPTION_MULTIPLIER = 100


@dataclass(frozen=True)
class OrderRequest:
    agent: str
    symbol: str                       # underlying ticker, upper case
    side: str                         # 'buy' | 'sell'
    qty: int                          # shares, or option contracts
    asset: str = "stock"              # 'stock' | 'option'
    option_type: str | None = None    # 'call' | 'put'
    strike: float | None = None
    expiration: str | None = None     # YYYY-MM-DD
    limit_price: float | None = None  # per share / per option share (premium)
    reason: str | None = None         # the agent's one-line why, for the log

    def __post_init__(self):
        object.__setattr__(self, "symbol", self.symbol.upper().strip())
        object.__setattr__(self, "side", self.side.lower())
        object.__setattr__(self, "asset", self.asset.lower())
        if self.option_type:
            object.__setattr__(self, "option_type", self.option_type.lower())

    @property
    def instrument(self) -> str:
        return instrument_key(self.symbol, self.asset, self.option_type, self.strike, self.expiration)

    @property
    def multiplier(self) -> int:
        return OPTION_MULTIPLIER if self.asset == "option" else 1

    def to_dict(self) -> dict:
        return asdict(self)


def instrument_key(symbol: str, asset: str, option_type: str | None = None,
                   strike: float | None = None, expiration: str | None = None) -> str:
    """'TSLA' for a share, 'TSLA 2026-12-18 400C' for an option."""
    if asset != "option":
        return symbol.upper()
    return f"{symbol.upper()} {expiration} {strike:g}{(option_type or '?')[0].upper()}"


def validate_shape(o: OrderRequest) -> str | None:
    """Structural problems, before any rule or quote is looked at."""
    if o.side not in ("buy", "sell"):
        return "side must be buy or sell"
    if o.asset not in ("stock", "option"):
        return "asset must be stock or option"
    if not isinstance(o.qty, int) or o.qty <= 0:
        return "qty must be a positive whole number"
    if not o.symbol.isalpha() or len(o.symbol) > 6:
        return "symbol must be a ticker like TSLA"
    if o.asset == "option":
        if o.option_type not in ("call", "put"):
            return "option_type must be call or put"
        if not o.strike or o.strike <= 0:
            return "option needs a positive strike"
        if not o.expiration or len(o.expiration) != 10:
            return "option needs expiration YYYY-MM-DD"
    if o.limit_price is not None and o.limit_price <= 0:
        return "limit_price must be positive"
    return None
