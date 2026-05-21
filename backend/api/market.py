"""BTC market data fetchers - Binance / CoinGecko / yfinance."""
from __future__ import annotations
import httpx
from datetime import datetime


async def fetch_binance(symbol: str = "BTCUSDT") -> dict:
    """Real-time BTC price + 24h stats from Binance public API."""
    async with httpx.AsyncClient(timeout=10) as c:
        ticker = (await c.get(f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}")).json()
        klines = (await c.get(
            f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1h&limit=24"
        )).json()
    closes = [float(k[4]) for k in klines]
    return {
        "source": "binance",
        "symbol": symbol,
        "price": float(ticker["lastPrice"]),
        "change_24h_pct": float(ticker["priceChangePercent"]),
        "high_24h": float(ticker["highPrice"]),
        "low_24h": float(ticker["lowPrice"]),
        "volume_24h": float(ticker["volume"]),
        "quote_volume_24h": float(ticker["quoteVolume"]),
        "klines_1h_close": closes,
        "fetched_at": datetime.utcnow().isoformat(),
    }


async def fetch_coingecko() -> dict:
    """Fallback: CoinGecko free API."""
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(
            "https://api.coingecko.com/api/v3/coins/bitcoin",
            params={"localization": "false", "tickers": "false", "community_data": "false"},
        )
        d = r.json()
    md = d.get("market_data", {})
    return {
        "source": "coingecko",
        "symbol": "BTC-USD",
        "price": md.get("current_price", {}).get("usd"),
        "change_24h_pct": md.get("price_change_percentage_24h"),
        "high_24h": md.get("high_24h", {}).get("usd"),
        "low_24h": md.get("low_24h", {}).get("usd"),
        "volume_24h": md.get("total_volume", {}).get("usd"),
        "fetched_at": datetime.utcnow().isoformat(),
    }


async def fetch_market(source: str = "binance", symbol: str = "BTCUSDT") -> dict:
    if source == "binance":
        try:
            return await fetch_binance(symbol)
        except Exception as e:
            return await fetch_coingecko()
    return await fetch_coingecko()


def format_market_summary(m: dict) -> str:
    """Human-readable summary fed to agents."""
    closes = m.get("klines_1h_close") or []
    trend = ""
    if len(closes) >= 2:
        delta = (closes[-1] - closes[0]) / closes[0] * 100
        trend = f" | 24h-trend (1h closes): {delta:+.2f}%"
    return (
        f"BTC Spot Price: ${m['price']:,.2f} | 24h Change: {m.get('change_24h_pct', 0):+.2f}% | "
        f"High: ${m.get('high_24h', 0):,.2f} | Low: ${m.get('low_24h', 0):,.2f} | "
        f"Volume: {m.get('volume_24h', 0):,.0f} BTC{trend} | Source: {m['source']}"
    )
