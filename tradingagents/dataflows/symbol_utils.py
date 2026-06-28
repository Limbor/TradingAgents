"""Symbol normalization and market-data error types for vendor calls.

Yahoo Finance (the default vendor) uses specific ticker conventions that
differ from the broker / TradingView / MT5 style symbols users often type:

    user types        Yahoo wants       why
    ---------------   ---------------   -----------------------------------
    XAUUSD, XAUUSD+   GC=F              gold has no forex pair on Yahoo;
                                        it is quoted as a COMEX future
    EURUSD            EURUSD=X          spot forex pairs take a ``=X`` suffix
    BTCUSD            BTC-USD           crypto pairs use a ``-`` separator
    SPX500, US500     ^GSPC             index CFDs map to Yahoo index symbols

Passing the raw broker symbol to Yahoo returns an empty result, which the
agents previously received as free text and could hallucinate a price
around (see issue #781). Centralizing the mapping here means every yfinance
entry point resolves symbols the same way, and new instruments are added by
appending a table row rather than editing call sites.
"""

from __future__ import annotations

import logging
import re

# NoMarketDataError lives in the vendor-error taxonomy (errors.py); re-exported
# here for the many call sites that import it alongside normalize_symbol.
from .errors import NoMarketDataError as NoMarketDataError

logger = logging.getLogger(__name__)


# ISO-4217 codes common enough to appear in retail forex pairs. A bare
# six-letter symbol whose halves are BOTH in this set is treated as a spot
# forex pair and given Yahoo's ``=X`` suffix.
_FOREX_CURRENCIES = frozenset(
    {
        "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD",
        "CNY", "CNH", "HKD", "SGD", "SEK", "NOK", "DKK", "PLN",
        "MXN", "ZAR", "TRY", "INR", "KRW", "BRL", "RUB", "THB",
    }
)

# Crypto bases that brokers quote against USD without a separator.
_CRYPTO_BASES = frozenset(
    {"BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "LTC", "BCH", "DOT", "AVAX", "LINK"}
)

# Explicit aliases for instruments whose broker symbol does not map to a
# Yahoo symbol by rule. Metals/energy resolve to their front-month future;
# index CFD names resolve to the underlying Yahoo index symbol. Extend by
# adding rows — no call site changes required.
_ALIASES = {
    # Precious metals (spot names -> COMEX/NYMEX futures)
    "XAUUSD": "GC=F", "XAU": "GC=F", "GOLD": "GC=F",
    "XAGUSD": "SI=F", "XAG": "SI=F", "SILVER": "SI=F",
    "XPTUSD": "PL=F", "XPDUSD": "PA=F",
    # Energy
    "WTICOUSD": "CL=F", "USOIL": "CL=F", "WTI": "CL=F",
    "BCOUSD": "BZ=F", "UKOIL": "BZ=F", "BRENT": "BZ=F",
    "NATGAS": "NG=F", "XNGUSD": "NG=F",
    "COPPER": "HG=F", "XCUUSD": "HG=F",
    # Index CFDs -> Yahoo index symbols
    "SPX500": "^GSPC", "US500": "^GSPC", "SPX": "^GSPC",
    "NAS100": "^NDX", "US100": "^NDX", "USTEC": "^NDX",
    "US30": "^DJI", "DJI30": "^DJI", "WS30": "^DJI",
    "GER40": "^GDAXI", "GER30": "^GDAXI", "DE40": "^GDAXI",
    "UK100": "^FTSE", "JP225": "^N225", "JPN225": "^N225",
    "FRA40": "^FCHI", "EU50": "^STOXX50E", "HK50": "^HSI",
}

# Yahoo symbols may contain letters, digits, and these structural characters.
_YAHOO_SAFE = re.compile(r"^[A-Za-z0-9._\-\^=]+$")


# Crypto quote currencies that all map to Yahoo's USD pair. Yahoo lists only
# ``<BASE>-USD`` (not the USDT/USDC stablecoin pairs), so a broker symbol quoted
# in any of these resolves to ``-USD`` (#982). Longest first so ``USDT``/``USDC``
# match before the ``USD`` substring.
_CRYPTO_QUOTES = ("USDT", "USDC", "USD")


def _normalize_crypto(s: str) -> str | None:
    """Return ``<BASE>-USD`` if ``s`` is a known crypto quoted in USD/USDT/USDC.

    Accepts dashed or undashed forms: ``BTCUSD``, ``BTCUSDT``, ``BTC-USDT``,
    ``BTC-USDC`` all resolve to ``BTC-USD``. Returns None otherwise.
    """
    compact = s.replace("-", "")
    for quote in _CRYPTO_QUOTES:
        if compact.endswith(quote):
            base = compact[: -len(quote)]
            if base in _CRYPTO_BASES:
                return f"{base}-USD"
            break
    return None


def normalize_symbol(raw: str) -> str:
    """Map a user/broker symbol to its canonical Yahoo Finance symbol.

    Resolution order (first match wins):
      1. Explicit alias table (metals, energy, index CFDs).
      2. Crypto rule: a known crypto base quoted in USD/USDT/USDC (dashed or
         not) -> ``BASE-USD``.
      3. Forex rule: six letters that are two ISO currency codes -> ``PAIR=X``.
      4. Otherwise the upper-cased symbol is returned unchanged (plain
         equities, ETFs, Yahoo-native symbols like ``GC=F`` or ``^GSPC``).

    A trailing ``+`` (broker CFD marker, e.g. ``XAUUSD+``) is stripped before
    matching. The function is purely syntactic — it performs no network
    calls — so it is safe to apply on every request.
    """
    if not isinstance(raw, str) or not raw.strip():
        return raw

    s = raw.strip().upper()
    # Broker CFD/qualifier suffixes Yahoo never uses.
    s = s.rstrip("+")

    crypto = _normalize_crypto(s)
    if s in _ALIASES:
        canonical = _ALIASES[s]
    elif detect_market(s) == "cn_a":
        canonical = normalize_for_yahoo_cn(s)
    elif crypto is not None:
        canonical = crypto
    elif len(s) == 6 and s[:3] in _FOREX_CURRENCIES and s[3:] in _FOREX_CURRENCIES:
        canonical = f"{s}=X"
    else:
        canonical = s

    if canonical != raw.strip().upper():
        logger.info("Resolved symbol %r to Yahoo symbol %r", raw, canonical)
    return canonical


def is_yahoo_safe(symbol: str) -> bool:
    """True when ``symbol`` only contains characters Yahoo symbols use."""
    return bool(symbol) and _YAHOO_SAFE.fullmatch(symbol) is not None


# ---------------------------------------------------------------------------
# A-share (CN market) helpers
# ---------------------------------------------------------------------------

_CN_EXCHANGE_MAP = {
    "6": "SH",   # Shanghai main board / STAR Market (688xxx)
    "0": "SZ",   # Shenzhen main board
    "3": "SZ",   # ChiNext (创业板)
    "8": "BJ",   # Beijing Stock Exchange (北交所)
    "4": "BJ",   # NEEQ / Beijing legacy
}

_CN_YAHOO_EXCHANGE_MAP = {
    "SH": "SS",
    "SS": "SS",
    "SZ": "SZ",
    "BJ": "BJ",
}


def detect_market(ticker: str) -> str:
    """Return ``"cn_a"`` for A-share tickers, ``"us"`` otherwise.

    Recognised CN forms:
      - 6-digit codes with or without exchange suffix (``000001``, ``600000.SH``)
      - Yahoo-style suffixes (``.SS``, ``.SZ``, ``.BJ``)
    """
    if not isinstance(ticker, str):
        return "us"
    t = ticker.strip().upper()
    if re.match(r"^\d{6}(\.(SH|SZ|BJ|SS))?$", t):
        return "cn_a"
    if t.endswith((".SS", ".SZ", ".BJ")):
        return "cn_a"
    return "us"


def _extract_cn_code(ticker: str) -> str:
    """Extract the bare 6-digit code from any CN ticker form."""
    t = ticker.strip().upper()
    m = re.match(r"^(\d{6})(\.(SH|SZ|BJ|SS))?$", t)
    if m:
        return m.group(1)
    return t


def normalize_cn_display(ticker: str) -> str:
    """Return a canonical ``<code>.<exchange>`` display string for CN tickers."""
    t = ticker.strip().upper()
    m = re.match(r"^(\d{6})(\.(SH|SZ|BJ|SS))?$", t)
    if m and m.group(3):
        exchange = "SH" if m.group(3) == "SS" else m.group(3)
        return f"{m.group(1)}.{exchange}"
    code = _extract_cn_code(ticker)
    if len(code) == 6 and code[0] in _CN_EXCHANGE_MAP:
        return f"{code}.{_CN_EXCHANGE_MAP[code[0]]}"
    return ticker.strip().upper()


def normalize_for_akshare(ticker: str) -> str:
    """Return the bare 6-digit code that AKShare APIs expect."""
    return _extract_cn_code(ticker)


def normalize_for_tushare(ticker: str) -> str:
    """Return ``<code>.<SH|SZ|BJ>`` in the form TuShare's ``ts_code`` accepts."""
    return normalize_cn_display(ticker)


def normalize_for_yahoo_cn(ticker: str) -> str:
    """Return Yahoo's A-share symbol form.

    Yahoo Finance uses ``.SS`` for Shanghai, while AKShare/TuShare use ``.SH``.
    Keeping the conversion here prevents each caller from guessing which suffix
    belongs to which vendor.
    """
    t = ticker.strip().upper()
    m = re.match(r"^(\d{6})(\.(SH|SZ|BJ|SS))?$", t)
    if m and m.group(3):
        exchange = _CN_YAHOO_EXCHANGE_MAP.get(m.group(3), m.group(3))
        return f"{m.group(1)}.{exchange}"
    code = _extract_cn_code(ticker)
    if len(code) != 6 or code[0] not in _CN_EXCHANGE_MAP:
        return ticker.strip().upper()
    exchange = _CN_EXCHANGE_MAP[code[0]]
    return f"{code}.{_CN_YAHOO_EXCHANGE_MAP.get(exchange, exchange)}"
