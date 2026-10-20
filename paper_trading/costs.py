"""What a round trip costs beyond the bid-ask spread: broker commission and
exchange fees (config `costs`).

The spread is already in the fills (buy at the ask, sell at the bid). What the
paper book left out until 2026-10-20 was the broker: at IBKR roughly $0.65 a
contract with a $1.00 minimum per order, so a one-lot round trip costs about
$2.10 - about 1.7% of a $1.23 SPY option, more than the spread itself (deep-dive
report, SIGNAL_RESEARCH). An option left to expire is never sold, so it pays
the buy-side order only.

Pure, no imports - storage and the research simulator both use it.
"""


def order_fee(contracts: int, costs: dict | None) -> float:
    """Commission (with the per-order minimum) plus exchange fees for ONE order."""
    if not costs or contracts <= 0:
        return 0.0
    commission = costs.get("commission_per_contract", 0.0) * contracts
    if commission > 0 or costs.get("min_per_order", 0.0) > 0:
        commission = max(commission, costs.get("min_per_order", 0.0))
    return commission + costs.get("exchange_fee_per_contract", 0.0) * contracts


def round_trip_fees(contracts: int, costs: dict | None, sold: bool = True) -> float:
    """Buy order plus sell order; just the buy when the option expired unsold."""
    return order_fee(contracts, costs) * (2 if sold else 1)
