"""Plain dataclasses mirroring the storage schema, decoupled from sqlite3.Row
so the engine logic can be unit tested without a database."""
from dataclasses import dataclass


@dataclass
class Position:
    id: int
    ticker: str
    option_type: str        # 'call' or 'put'
    strike: float
    expiration: str
    contracts: int
    entry_price: float
    cost_basis: float
    entry_composite_score: float
    status: str = "open"
    suggested_exit_reason: str | None = None
    current_price: float | None = None
    # per-trade exit targets (None = that trigger is off for this position);
    # profit_target_pct is positive, stop_loss_pct is negative
    profit_target_pct: float | None = None
    stop_loss_pct: float | None = None
    opened_by: str = "manual"   # 'manual' | 'auto'

    @classmethod
    def from_row(cls, row) -> "Position":
        return cls(
            id=row["id"], ticker=row["ticker"], option_type=row["option_type"],
            strike=row["strike"], expiration=row["expiration"], contracts=row["contracts"],
            entry_price=row["entry_price"], cost_basis=row["cost_basis"],
            entry_composite_score=row["entry_composite_score"], status=row["status"],
            suggested_exit_reason=row["suggested_exit_reason"],
            current_price=row["current_price"],
            profit_target_pct=row["profit_target_pct"],
            stop_loss_pct=row["stop_loss_pct"],
            opened_by=row["opened_by"],
        )
