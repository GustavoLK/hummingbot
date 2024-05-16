import logging
from decimal import Decimal
from typing import Dict

from hummingbot.connector.connector_base import ConnectorBase

from hummingbot.client.hummingbot_application import HummingbotApplication
from hummingbot.core.data_type.common import OrderType, PositionMode
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
from hummingbot.strategy.strategy_py_base import (
    BuyOrderCompletedEvent,
    BuyOrderCreatedEvent,
    OrderFilledEvent,
    SellOrderCompletedEvent,
    SellOrderCreatedEvent, OrderCancelledEvent,
)
from glk.Notificator import Notificator


class SimpleOrder(ScriptStrategyBase):
    """
    This example script places an order on a Hummingbot exchange connector. The user can select the
    order type (market or limit), side (buy or sell) and the spread (for limit orders only).
    The bot uses the Rate Oracle to convert the order amount in USD to the base amount for the exchange and trading pair.
    The script uses event handlers to notify the user when the order is created and completed, and then stops the bot.
    """

    # Key Parameters
    exchange = "binance_perpetual"
    order_amount_usd = Decimal(30)
    leverage = 5

    trading_pair = "BTC-USDT"
    base = "BTC"
    quote = "USDT"

    side = "buy"
    order_type = "limit"   # market or limit
    spread = 0  # for limit orders only

    profitTarget = 0.20


    # Internals
    position_status = "ready"
    order_id = None
    entryPrice = None
    positionAmount = None


    counter = 0

    # Other Parameters
    order_created = False
    markets = {
        exchange: {f"{base}-{quote}"}
    }

    def __init__(self, connectors: Dict[str, ConnectorBase]):
        super().__init__(connectors)
        self.set_leverage()

    def set_leverage(self) -> None:
        perp_connector = self.connectors[self.exchange]
        perp_connector.set_position_mode(PositionMode.ONEWAY)
        perp_connector.set_leverage(trading_pair=self.trading_pair, leverage=self.leverage)
        self.logger().info(
            f"Setting leverage to {self.leverage}x for {self.exchange} on {self.trading_pair}"
        )

    def on_tick(self):
        if self.position_status == "ready":
            self.create_order()
            return
        if self.position_status == "opened":
            price = self.connectors[self.exchange].get_mid_price(f"{self.base}-{self.quote}")
            priceDelta = (price - self.entryPrice) * (1 if self.side == "buy" else -1)
            positioDelta = self.positionAmount * priceDelta
            msg = f"TICK Entry: {self.entryPrice} Actual: {price} Price var: {priceDelta} Position var {positioDelta}"
            self.logger().info(msg)

            if positioDelta > self.profitTarget:
                self.create_close_order()
        if self.position_status == "closed":
            HummingbotApplication.main_application().stop()




        # if self.order_id is not None:
        #     self.counter += 1
        #     msg = f"TICK [{self.counter}]"
        #     if self.counter >= 30:
        #         self.cancel(connector_name=self.exchange,
        #                     trading_pair=f"{self.base}-{self.quote}",
        #                     order_id=self.order_id)
        #         self.order_id = None
        #         msg += " ORDER CANCELED"
        #
        #     self.logger().info(msg)






    def create_order(self):
        price = self.connectors[self.exchange].get_mid_price(f"{self.base}-{self.quote}")
        amount = (self.order_amount_usd * self.leverage) / price

        # applies spread to price if order type is limit
        order_type = OrderType.MARKET if self.order_type == "market" else OrderType.LIMIT_MAKER
        if order_type == OrderType.LIMIT_MAKER and self.side == "buy":
            price = price - self.spread
        else:
            if order_type == OrderType.LIMIT_MAKER and self.side == "sell":
                price = price + self.spread

        self.logger().info(f"Creating Order with current price of {price}: {order_type} {self.side} Limit {price}")

        # places order
        Notificator().notify("Order created", "Nada")
        if self.side == "sell":
            self.position_status = "opening"
            self.sell(
                connector_name=self.exchange,
                trading_pair=f"{self.base}-{self.quote}",
                amount=amount,
                order_type=order_type,
                price=price
            )
        else:
            self.position_status = "opening"
            self.buy(
                connector_name=self.exchange,
                trading_pair=f"{self.base}-{self.quote}",
                amount=amount,
                order_type=order_type,
                price=price
            )
        self.order_created = True



    def create_close_order(self):
        price = self.connectors[self.exchange].get_mid_price(f"{self.base}-{self.quote}")
        amount = (self.order_amount_usd * self.leverage) / price

        self.logger().info(f"Creating Closing Market Order with current price of {price}")

        if self.positionAmount > 0:
            self.position_status = "closing"
            self.sell(
                connector_name=self.exchange,
                trading_pair=f"{self.base}-{self.quote}",
                amount=self.positionAmount,
                order_type=OrderType.MARKET,
                price=price
            )
        if self.positionAmount < 0:
            self.position_status = "closing"
            self.buy(
                connector_name=self.exchange,
                trading_pair=f"{self.base}-{self.quote}",
                amount=self.positionAmount,
                order_type=OrderType.MARKET,
                price=price
            )






    def did_fill_order(self, event: OrderFilledEvent):
        msg = (f"{event.trade_type.name} {event.amount} of {event.trading_pair} {self.exchange} at {event.price}")
        self.log_with_clock(logging.INFO, msg)
        self.notify_hb_app_with_timestamp(msg)
        self.entryPrice = event.price
        self.positionAmount = event.amount * (1 if self.side == "buy" else -1)
        if self.position_status == "opening":
            self.position_status = "opened"
        if self.position_status == "closing":
            self.position_status = "closed"

        Notificator().notify("Order filled", msg)


    def did_complete_buy_order(self, event: BuyOrderCompletedEvent):
        msg = (f"did_complete_buy_order Order {event.order_id} to buy {event.base_asset_amount} of {event.base_asset} is completed.")
        self.log_with_clock(logging.INFO, msg)
        self.notify_hb_app_with_timestamp(msg)

    def did_complete_sell_order(self, event: SellOrderCompletedEvent):
        msg = (f"did_complete_sell_order Order {event.order_id} to sell {event.base_asset_amount} of {event.base_asset} is completed.")
        self.log_with_clock(logging.INFO, msg)
        self.notify_hb_app_with_timestamp(msg)

    def did_create_buy_order(self, event: BuyOrderCreatedEvent):
        self.order_id = event.order_id
        msg = (f"did_create_buy_order Created BUY order {event.order_id}")
        self.log_with_clock(logging.INFO, msg)
        self.notify_hb_app_with_timestamp(msg)

    def did_create_sell_order(self, event: SellOrderCreatedEvent):
        self.order_id = event.order_id
        msg = (f"did_create_sell_order Created SELL order {event.order_id}")
        self.log_with_clock(logging.INFO, msg)
        self.notify_hb_app_with_timestamp(msg)

    def did_cancel_order(self, event: OrderCancelledEvent):
        """
        Method called when the connector notifies an order has been cancelled
        """
        self.logger().info(f"did_cancel_order The order {event.order_id} has been cancelled")




