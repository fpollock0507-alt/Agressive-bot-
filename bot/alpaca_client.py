"""Alpaca client wrapper with both stock + options support.

Stocks are used to read the underlying (SPY/QQQ) for signal generation.
Options are what we actually trade.
"""
from __future__ import annotations

from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import (
    StockBarsRequest,
    StockLatestQuoteRequest,
    OptionLatestQuoteRequest,
)
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (
    OrderSide,
    TimeInForce,
    AssetStatus,
    ContractType,
    QueryOrderStatus,
)
from alpaca.trading.requests import (
    MarketOrderRequest,
    LimitOrderRequest,
    GetOptionContractsRequest,
    GetOrdersRequest,
)

from .config import Credentials

ET = ZoneInfo("America/New_York")


class AlpacaClient:
    def __init__(self, creds: Credentials):
        self.trading = TradingClient(creds.api_key, creds.api_secret, paper=creds.paper)
        self.stock_data = StockHistoricalDataClient(creds.api_key, creds.api_secret)
        self.option_data = OptionHistoricalDataClient(creds.api_key, creds.api_secret)

    # ----- account -----
    def account(self):
        return self.trading.get_account()

    def equity(self) -> float:
        return float(self.account().equity)

    def cash(self) -> float:
        return float(self.account().cash)

    def buying_power(self) -> float:
        return float(self.account().buying_power)

    def options_buying_power(self) -> float:
        # For long options we cannot use margin — must be cash-funded.
        return float(self.account().cash)

    # ----- positions / orders -----
    def positions(self):
        return self.trading.get_all_positions()

    def option_positions(self):
        return [p for p in self.positions() if p.asset_class == "us_option"]

    def open_orders(self):
        return self.trading.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN)
        )

    def cancel_all_orders(self):
        return self.trading.cancel_orders()

    def close_all_positions(self):
        return self.trading.close_all_positions(cancel_orders=True)

    def close_position(self, symbol: str):
        return self.trading.close_position(symbol)

    # ----- market clock -----
    def is_market_open(self) -> bool:
        return self.trading.get_clock().is_open

    def market_clock(self):
        return self.trading.get_clock()

    # ----- stock data (underlying) -----
    def daily_bars(self, symbol: str, days: int = 30) -> pd.DataFrame:
        end = datetime.now(ET) - timedelta(minutes=15)
        start = end - timedelta(days=days * 2 + 10)
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
        )
        bars = self.stock_data.get_stock_bars(req).df
        if bars.empty:
            return bars
        if isinstance(bars.index, pd.MultiIndex):
            bars = bars.xs(symbol, level="symbol")
        return bars.tail(days)

    def minute_bars(self, symbol: str, lookback_minutes: int = 60) -> pd.DataFrame:
        end = datetime.now(ET) - timedelta(minutes=16)
        start = end - timedelta(minutes=lookback_minutes + 30)
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame(1, TimeFrameUnit.Minute),
            start=start,
            end=end,
        )
        bars = self.stock_data.get_stock_bars(req).df
        if bars.empty:
            return bars
        if isinstance(bars.index, pd.MultiIndex):
            bars = bars.xs(symbol, level="symbol")
        return bars

    def latest_stock_quote(self, symbol: str):
        req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        return self.stock_data.get_stock_latest_quote(req)[symbol]

    def latest_stock_price(self, symbol: str) -> float:
        q = self.latest_stock_quote(symbol)
        # Mid of NBBO; falls back to ask if bid is 0.
        if q.bid_price and q.ask_price:
            return (float(q.bid_price) + float(q.ask_price)) / 2
        return float(q.ask_price or q.bid_price or 0)

    # ----- options chain + quotes -----
    def get_option_chain(
        self,
        underlying: str,
        expiration: date,
        contract_type: ContractType,
    ) -> list:
        """Fetch tradable option contracts for `underlying` expiring on `expiration`."""
        req = GetOptionContractsRequest(
            underlying_symbols=[underlying],
            status=AssetStatus.ACTIVE,
            expiration_date=expiration,
            type=contract_type,
            limit=500,
        )
        resp = self.trading.get_option_contracts(req)
        return resp.option_contracts or []

    def latest_option_quote(self, symbol: str):
        req = OptionLatestQuoteRequest(symbol_or_symbols=symbol)
        return self.option_data.get_option_latest_quote(req)[symbol]

    def option_mid(self, symbol: str) -> tuple[float, float, float] | None:
        """Return (bid, ask, mid) for an option contract. None if no quote."""
        try:
            q = self.latest_option_quote(symbol)
            bid = float(q.bid_price or 0)
            ask = float(q.ask_price or 0)
            if bid <= 0 or ask <= 0:
                return None
            return bid, ask, (bid + ask) / 2
        except Exception:
            return None

    # ----- option order submission -----
    def submit_option_market(self, symbol: str, qty: int, side: OrderSide):
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,
        )
        return self.trading.submit_order(req)

    def submit_option_limit(
        self, symbol: str, qty: int, side: OrderSide, limit_price: float
    ):
        req = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side,
            time_in_force=TimeInForce.DAY,
            limit_price=round(limit_price, 2),
        )
        return self.trading.submit_order(req)
