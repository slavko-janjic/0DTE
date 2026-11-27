"""Find stock tickers in free text (Reddit titles, headlines).

Two rules, tuned against r/wallstreetbets' habits:
- a cashtag ($TSLA, $nvda) counts when it is a known symbol, any length;
- a bare upper-case word counts only when it is a known symbol of 2-5
  letters AND not an everyday word or WSB slang that happens to be a ticker
  (A, IT, ALL, DD, YOLO, CEO, ...). Single letters need the $.
"""
from __future__ import annotations

import re

_CASHTAG = re.compile(r"\$([A-Za-z]{1,5})(?![A-Za-z])")
_BARE = re.compile(r"(?<![A-Za-z$])([A-Z]{2,5})(?![A-Za-z])")

# Upper-case words that are (or could be) tickers but almost never mean the
# stock when written bare. Extend as false positives show up in the digests.
STOPWORDS = frozenset("""
A I AM AN AND ANY ARE AS AT BE BIG BY CAN CEO CFO COO CTO DD DO EOD EOW EPS ETF
FED FOMC FOR FUD GDP GO HAS HE HOLD IF IMO IN IPO IS IT ITM ATM OTM IV JUST LOL
ME MOM MY NEW NO NOT NOW OF OK ON ONE OP OR OUT PM AH PT RH SEC SO THE TO UP US
USA USD WSB YOLO YOY QOQ CPI PPI PCE GDP AI EV EVS ER ALL ARE BRO CASH CALL CALLS
PUT PUTS LONG SHORT BUY SELL BEAT MISS HOT REAL GOOD NEXT LOW HIGH OPEN PLAY
RUN SEE SAY WHY WHO WAY WELL TV PC UK EU API FAQ TLDR EDIT POST NEWS DOW LMAO
TA TD UI UX VC VR AR CD DM GG HR IRS IQ JOB KEY LIVE LOVE MAN MAX MIN OG PE PS
""".split())


def extract_tickers(text: str | None, universe: set[str],
                    always: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """Tickers mentioned in `text`, sorted and unique. `universe` is the set
    of known symbols; `always` are watchlist symbols that count bare even if
    they are on the stoplist (QQQ, SPY are never ambiguous)."""
    if not text:
        return []
    known = universe | set(always)
    found = set()
    for m in _CASHTAG.finditer(text):
        t = m.group(1).upper()
        if t in known:
            found.add(t)
    for m in _BARE.finditer(text):
        t = m.group(1)
        if t in always or (t in universe and t not in STOPWORDS):
            found.add(t)
    return sorted(found)
