"""Kraken Pro public market data adapter (REST).

Primary crypto data source (per user decision 2026-08-28). Public endpoints, **no API key**.

Endpoints used:
* ``/0/public/OHLC``   -> confirmed candles  (row: [time, open, high, low, close, vwap, volume, count])
* ``/0/public/Trades`` -> public trades      (row: [price, volume, time, side, ord_type, misc, id])

Kraken pair naming is irregular (``XXBTZUSD`` for BTC/USD spot). We map canonical symbols and,
on the way back, take the single non-``last`` key from ``result``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from trading_agent.core.clock import Clock, SystemClock
from trading_agent.core.enums import DataKind, Side, Timeframe
from trading_agent.core.models import OHLCV, Quote, Trade
from trading_agent.core.time import bar_close_time, ensure_utc, parse_timestamp
from trading_agent.data.health import HealthTracker
from trading_agent.data.interfaces import (
    AsyncOHLCVSource,
    AsyncQuoteSource,
    AsyncTradeSource,
    ProviderStatus,
)
from trading_agent.data.quality import sort_ohlcv
from trading_agent.net.client import HttpClient, NetError

_INTERVAL_MIN: dict[Timeframe, int] = {
    Timeframe.M1: 1,
    Timeframe.M5: 5,
    Timeframe.M15: 15,
    Timeframe.M30: 30,
    Timeframe.H1: 60,
    Timeframe.H4: 240,
    Timeframe.D1: 1440,
    Timeframe.W1: 10080,
}

# canonical symbol -> Kraken pair
_PAIR: dict[str, str] = {
    "BTCUSDT": "XBTUSDT",
    "ETHUSDT": "ETHUSDT",
    "SOLUSDT": "SOLUSDT",
    "BTCUSD": "XXBTZUSD",
    "ETHUSD": "XETHZUSD",
}


class KrakenDataProvider(AsyncOHLCVSource, AsyncTradeSource, AsyncQuoteSource):
    name = "kraken"
    provides = frozenset({DataKind.OHLCV, DataKind.TRADE, DataKind.QUOTE})

    def __init__(
        self,
        *,
        client: HttpClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._clock = clock or SystemClock()
        self._client = client or HttpClient(
            "https://api.kraken.com",
            name=self.name,
            # 2 Anfragen je Sekunde statt 1. Der Scan holt je Wert vier Zeitebenen;
            # bei 60 Paaren sind das 240 Abrufe, und bei 1/s dauert allein das Holen
            # vier Minuten — zu lang fuer einen Lauf alle 15 Minuten. Kraken erlaubt
            # auf den oeffentlichen Endpunkten mehr; 2/s bleibt deutlich darunter.
            rate_per_sec=2.0,
            transport=transport,
        )
        self._health = HealthTracker(self.name, clock=self._clock)
        #: kanonischer Name -> Kraken-Paarname, gefuellt von :meth:`list_symbol_info`
        self._gelernt: dict[str, str] = {}
        #: jeder Kraken-Schreibweise (Schluessel UND altname) ihr kanonischer Name.
        #: Kraken fuehrt dasselbe Paar unter zwei Namen — der Ticker antwortet unter dem
        #: einen, die Paarliste nennt den anderen. Ohne beide Eintraege fielen
        #: ausgerechnet die aeltesten und groessten Paare (BTC, ETH) aus dem Universum.
        self._rueck: dict[str, str] = {}

    def status(self) -> ProviderStatus:
        return self._health.status()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _pair(self, instrument: str) -> str:
        """Kanonischer Name -> Kraken-Paarname.

        Kraken hat zwei Schreibweisen fuer dasselbe Paar (``XXBTZEUR`` und ``XBTEUR``)
        und nennt Bitcoin ``XBT``. Die Zuordnung lernt der Anbieter beim Aufbau des
        Universums selbst (:meth:`list_symbol_info`) — eine von Hand gepflegte Tabelle
        waere nach dem naechsten neuen Paar wieder unvollstaendig.
        """
        name = instrument.upper()
        gelernt = self._gelernt.get(name)
        if gelernt:
            return gelernt
        return _PAIR.get(name, name)

    @staticmethod
    def _muenze(roh: str) -> str:
        """``XXBT`` -> ``BTC``, ``ZEUR`` -> ``EUR``.

        Kraken haengt historisch ein ``X`` an Kryptowaehrungen und ein ``Z`` an
        Landeswaehrungen, und nennt Bitcoin ``XBT``. Das steht in keiner Watchlist und
        in keinem Depot — nach aussen heisst es hier so, wie es ueberall sonst heisst.
        """
        m = roh.upper()
        if len(m) == 4 and m[0] in "XZ":
            m = m[1:]
        return "BTC" if m == "XBT" else ("DOGE" if m == "XDG" else m)

    @staticmethod
    def _unwrap(payload: dict[str, Any]) -> dict[str, Any]:
        errors = payload.get("error") or []
        if errors:
            raise NetError(f"kraken error: {errors}")
        result: dict[str, Any] = payload.get("result", {})
        return result

    async def fetch_ohlcv(
        self, instrument: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[OHLCV]:
        if timeframe not in _INTERVAL_MIN:
            raise ValueError(f"kraken: unsupported timeframe {timeframe}")
        start = ensure_utc(start)
        end = ensure_utc(end)
        params = {
            "pair": self._pair(instrument),
            "interval": _INTERVAL_MIN[timeframe],
            "since": int(start.timestamp()) - 1,
        }
        try:
            result = self._unwrap(await self._client.get_json("/0/public/OHLC", params))
        except Exception as exc:
            self._health.record_failure(str(exc))
            raise
        rows: list[Any] = next((v for k, v in result.items() if k != "last"), [])
        out: list[OHLCV] = []
        for r in rows:
            open_time = parse_timestamp(int(r[0]))
            close_time = bar_close_time(open_time, timeframe)
            if not (start <= open_time < end) or close_time > end:
                continue
            o, h, low, c = float(r[1]), float(r[2]), float(r[3]), float(r[4])
            out.append(
                OHLCV(
                    instrument=instrument.upper(),
                    timeframe=timeframe,
                    open_time=open_time,
                    close_time=close_time,
                    open=o,
                    high=max(h, o, c),
                    low=min(low, o, c),
                    close=c,
                    volume=float(r[6]),
                    quote_volume=float(r[5]) * float(r[6]) if len(r) > 5 else None,
                    trades=int(r[7]) if len(r) > 7 else None,
                    source=self.name,
                    ingested_at=self._clock.now(),
                )
            )
        self._health.record_success(latency_ms=1.0)
        return sort_ohlcv(out)

    async def fetch_quote(self, instrument: str) -> Quote:
        """Bester Bid/Ask über ``/0/public/Ticker``. Kraken liefert keinen Zeitstempel im
        Ticker ⇒ ``ts`` = Empfangszeit (``clock.now()``). Keine Zukunftsdaten möglich."""
        params = {"pair": self._pair(instrument)}
        try:
            result = self._unwrap(await self._client.get_json("/0/public/Ticker", params))
        except Exception as exc:
            self._health.record_failure(str(exc))
            raise
        row = next(iter(result.values()), None)
        if not row or "a" not in row or "b" not in row:
            self._health.record_failure("kraken ticker: kein a/b im Ergebnis")
            raise NetError(f"kraken ticker ohne Bid/Ask für {instrument}")
        now = ensure_utc(self._clock.now())
        self._health.record_success(latency_ms=1.0)
        return Quote(
            instrument=instrument.upper(),
            ts=now,
            bid=float(row["b"][0]),
            ask=float(row["a"][0]),
            bid_size=float(row["b"][2]) if len(row["b"]) > 2 else None,
            ask_size=float(row["a"][2]) if len(row["a"]) > 2 else None,
            source=self.name,
            ingested_at=now,
        )

    # ---- Universum: Symbolliste und 24-h-Ticker ----------------------
    #
    # Damit wird Kraken zur Quelle des Krypto-Universums. Vorher kam es von Binance —
    # und das war kein Schoenheitsfehler: gescannt wurden Paare, die Ozan bei seinen
    # Boersen gar nicht kaufen kann. Ein Signal auf ein Paar, das es dort nicht gibt,
    # ist kein Signal, sondern Arbeit fuer nichts.
    #
    # Kraken statt Bybit als Quelle hat einen simplen Grund: Bybit sperrt die Abfrage
    # aus mehreren Laendern per CloudFront (HTTP 403), auch aus der CI. Was sich nicht
    # abrufen laesst, taugt nicht als Grundlage — unabhaengig davon, wie gut die Boerse
    # sonst ist. Dieselben Coins stehen bei Bybit ohnehin als USDT-Paar bereit.

    async def list_symbol_info(self, *, quote: str | None = None) -> list[dict[str, Any]]:
        """Handelbare Paare mit Basis und Quote, in kanonischer Schreibweise.

        Nebenwirkung mit Absicht: die Zuordnung kanonisch -> Kraken-Paarname wird
        gemerkt, damit :meth:`fetch_ohlcv` danach mit ``BTCEUR`` aufgerufen werden kann.
        """
        try:
            result = self._unwrap(await self._client.get_json("/0/public/AssetPairs", {}))
        except Exception as exc:
            self._health.record_failure(str(exc))
            raise
        aus: list[dict[str, Any]] = []
        for kraken_name, row in result.items():
            if not isinstance(row, dict) or row.get("status") != "online":
                continue
            b, q = str(row.get("base") or ""), str(row.get("quote") or "")
            if not b or not q:
                continue
            basis, waehrung = self._muenze(b), self._muenze(q)
            if quote and waehrung != quote.upper():
                continue
            name = f"{basis}{waehrung}"
            altname = str(row.get("altname") or kraken_name)
            self._gelernt[name] = altname
            self._rueck[kraken_name] = name
            self._rueck[altname] = name
            aus.append(
                {"instrument": name, "basis": basis, "quote": waehrung, "spot": True}
            )
        self._health.record_success(latency_ms=1.0)
        return aus

    async def fetch_ticker_24h_all(self) -> list[dict[str, Any]]:
        """Alle Ticker in einem Aufruf, auf dieselben Felder gebracht wie anderswo.

        Kraken liefert den Umsatz nicht direkt: ``v[1]`` ist das Volumen in der Basis,
        ``p[1]`` der volumengewichtete Schnitt. Das Produkt ist der Umsatz in der
        Quote-Waehrung — dieselbe Groesse, die der Universumsfilter erwartet. Die Zahl
        der Abschluesse steht in ``t[1]`` und wird mitgenommen: sie trennt echten Umsatz
        von wenigen Grossorders.
        """
        try:
            result = self._unwrap(await self._client.get_json("/0/public/Ticker", {}))
        except Exception as exc:
            self._health.record_failure(str(exc))
            raise
        rueck = self._rueck
        aus: list[dict[str, Any]] = []
        for kraken_name, row in result.items():
            if not isinstance(row, dict):
                continue
            name = rueck.get(kraken_name)
            if name is None:
                continue
            try:
                vol = float(row["v"][1])
                vwap = float(row["p"][1])
                aus.append(
                    {
                        "instrument": name,
                        "last": float(row["c"][0]),
                        "high": float(row["h"][1]),
                        "low": float(row["l"][1]),
                        "quote_volume": vol * vwap,
                        "price_change_pct": (
                            (float(row["c"][0]) / float(row["o"]) - 1.0) * 100.0
                            if float(row.get("o") or 0) > 0
                            else 0.0
                        ),
                        "trades": int(row["t"][1]),
                    }
                )
            except (KeyError, TypeError, ValueError, IndexError):
                continue
        self._health.record_success(latency_ms=1.0)
        return aus

    async def fetch_trades(self, instrument: str, start: datetime, end: datetime) -> list[Trade]:
        start = ensure_utc(start)
        end = ensure_utc(end)
        params = {"pair": self._pair(instrument), "since": int(start.timestamp() * 1_000_000_000)}
        try:
            result = self._unwrap(await self._client.get_json("/0/public/Trades", params))
        except Exception as exc:
            self._health.record_failure(str(exc))
            raise
        rows: list[Any] = next((v for k, v in result.items() if k != "last"), [])
        out: list[Trade] = []
        for i, r in enumerate(rows):
            ts = parse_timestamp(float(r[2]))
            if not (start <= ts < end):
                continue
            out.append(
                Trade(
                    instrument=instrument.upper(),
                    ts=ts,
                    price=float(r[0]),
                    size=float(r[1]),
                    side=Side.BUY if r[3] == "b" else Side.SELL,
                    trade_id=str(r[6]) if len(r) > 6 else f"{instrument}-{i}",
                    source=self.name,
                    ingested_at=self._clock.now(),
                )
            )
        self._health.record_success()
        return out


__all__ = ["KrakenDataProvider"]
