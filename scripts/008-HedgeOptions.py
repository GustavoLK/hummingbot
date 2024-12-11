from datetime import datetime
from decimal import Decimal
from typing import Dict
from enum import Enum, auto

from hummingbot.strategy.strategy_v2_base import StrategyV2Base

from hummingbot.client.hummingbot_application import HummingbotApplication
from hummingbot.connector.connector_base import ConnectorBase

from hummingbot.core.event.events import BuyOrderCompletedEvent, OrderFilledEvent, SellOrderCompletedEvent

from hummingbot.core.data_type.common import TradeType, OrderType, PositionMode

from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
from glk.Notificator import Notificator


class HedgingStatus(Enum):
    WAITING = auto()
    OPENING = auto()
    OPENED = auto()
    STOP_LOSSING = auto()
    CLOSING = auto()
    CLOSED = auto()


class OperationType(Enum):
    OPEN = auto()
    TAKE_PROFIT = auto()
    STOP_LOSS = auto()


class LogPricesExample(StrategyV2Base):
    markets = {
        "hyperliquid_perpetual": {"AAVE-USD"}
    }

    side = TradeType.SELL
    trigger_value = 131.30
    token_amount = Decimal('0.1')
    leverage = 4
    target_price_pct = Decimal('0.001')
    exit_time_str = "2024-11-26 22:18:00"

    actual_price = None
    fill_price = None
    hedging_status = HedgingStatus.WAITING
    exit_time = None

    def __init__(self, connectors: Dict[str, ConnectorBase]):
        super().__init__(connectors)
        self.exit_time = datetime.strptime(self.exit_time_str, "%Y-%m-%d %H:%M:%S")
        self.set_leverage()

    def set_leverage(self) -> None:
        connector_name = list(self.markets.keys())[0]
        market = list(self.markets.values())[0]
        pair = list(market)[0]

        perp_connector = self.connectors[connector_name]
        perp_connector.set_position_mode(PositionMode.ONEWAY)
        perp_connector.set_leverage(trading_pair=pair, leverage=self.leverage)
        self.logger().info(
            f"Setting leverage to {self.leverage}x for {connector_name} on {pair}"
        )

    def on_tick(self):
        connector_name = list(self.markets.keys())[0]
        market = list(self.markets.values())[0]
        pair = list(market)[0]
        self.actual_price = self.connectors[connector_name].get_mid_price(pair)

        current_time = datetime.now()

        if self.actual_price is not None and self.hedging_status == HedgingStatus.WAITING:
            self.logger().info(f"{pair}: {self.actual_price}")
            if self.side == TradeType.BUY and self.actual_price > self.trigger_value:
                self.create_order()
            elif self.side == TradeType.SELL and self.actual_price < self.trigger_value:
                self.create_order()
        elif self.actual_price is not None and self.hedging_status == HedgingStatus.OPENED:
            if current_time > self.exit_time:
                if self.side == TradeType.BUY:
                    target_price = self.fill_price * (1 + self.target_price_pct)
                    if self.actual_price > target_price:
                        self.logger().info(f"{pair}: {self.actual_price} > {target_price} CLOSING")
                        self.create_order(OperationType.TAKE_PROFIT)
                    else:
                        self.logger().info(f"{pair}: {self.actual_price} <= {target_price}")
                elif self.side == TradeType.SELL:
                    target_price = self.fill_price * (1 - self.target_price_pct)
                    if self.actual_price < target_price:
                        self.logger().info(f"{pair}: {self.actual_price} < {target_price} CLOSING")
                        self.create_order(OperationType.TAKE_PROFIT)
                    else:
                        self.logger().info(f"{pair}: {self.actual_price} >= {target_price}")
            else:
                pass

    def create_order(self, operation_type: OperationType = OperationType.OPEN):
        connector_name = list(self.markets.keys())[0]
        market = list(self.markets.values())[0]
        pair = list(market)[0]
        if operation_type == OperationType.OPEN:
            self.hedging_status = HedgingStatus.OPENING
        elif operation_type == OperationType.TAKE_PROFIT:
            self.hedging_status = HedgingStatus.CLOSING
        elif operation_type == OperationType.STOP_LOSS:
            self.hedging_status = HedgingStatus.STOP_LOSSING

        str_log = f"Creating Order with current price of {self.actual_price}: {self.side}"
        self.logger().info(str_log)

        if self.side == TradeType.BUY or (self.side == TradeType.SELL and operation_type != OperationType.OPEN):
            self.buy(
                connector_name=connector_name,
                trading_pair=pair,
                amount=self.token_amount,
                order_type=OrderType.MARKET,
                price=self.actual_price
            )
        elif self.side == TradeType.SELL or (self.side == TradeType.BUY and operation_type != OperationType.OPEN):
            self.sell(
                connector_name=connector_name,
                trading_pair=pair,
                amount=self.token_amount,
                order_type=OrderType.MARKET,
                price=self.actual_price
            )

    def did_fill_order(self, event: OrderFilledEvent):
        if self.hedging_status == HedgingStatus.OPENING:
            self.hedging_status = HedgingStatus.OPENED
        elif self.hedging_status == HedgingStatus.STOP_LOSSING:
            self.hedging_status = HedgingStatus.WAITING
        else:
            self.hedging_status = HedgingStatus.CLOSED
        msg = f"{self.hedging_status}  {event.trade_type.name} {event.amount} of {event.trading_pair} at {event.price}"
        self.fill_price = event.price
        Notificator().notify("Order filled", msg)
        if self.hedging_status == HedgingStatus.CLOSED:
            HummingbotApplication.main_application().stop()

    def did_complete_buy_order(self, event: BuyOrderCompletedEvent):
        msg = f"did_complete_buy_order Order {event.order_id} to buy {event.base_asset_amount} of {event.base_asset} is completed."
        self.logger().info(msg)

    def did_complete_sell_order(self, event: SellOrderCompletedEvent):
        msg = f"did_complete_sell_order Order {event.order_id} to sell {event.base_asset_amount} of {event.base_asset} is completed."
        self.logger().info(msg)
