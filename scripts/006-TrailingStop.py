import os
from datetime import datetime
from decimal import Decimal
from typing import Dict, List

import pandas_ta as ta  # noqa: F401
from pydantic import Field, validator

from glk.Notificator import Notificator
from hummingbot.client.config.config_data_types import ClientFieldData
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.clock import Clock
from hummingbot.core.data_type.common import OrderType, PositionMode, PriceType, TradeType
from hummingbot.core.event.events import BuyOrderCreatedEvent, OrderFilledEvent, SellOrderCreatedEvent
from hummingbot.data_feed.candles_feed.candles_factory import CandlesConfig
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase
from hummingbot.strategy_v2.executors.position_executor.data_types import (
    PositionExecutorConfig,
    TrailingStop,
    TripleBarrierConfig,
)
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, StopExecutorAction


class GLKTrailingStopConfig(StrategyV2ConfigBase):
    script_file_name: str = Field(default_factory=lambda: os.path.basename(__file__))
    candles_config: List[CandlesConfig] = []

    order_amount_quote: Decimal = Field(
        default=25, gt=0,
        client_data=ClientFieldData(
            prompt=lambda mi: "Enter the amount of quote asset to be used per order (e.g. 25): ",
            prompt_on_new=True))
    leverage: int = Field(
        default=20, gt=0,
        client_data=ClientFieldData(
            prompt=lambda mi: "Enter the leverage (e.g. 20): ",
            prompt_on_new=True))
    position_mode: PositionMode = Field(
        default="HEDGE",
        client_data=ClientFieldData(
            prompt=lambda mi: "Enter the position mode (HEDGE/ONEWAY): ",
            prompt_on_new=True
        )
    )

    # Triple Barrier Configuration
    stop_loss: Decimal = Field(
        default=Decimal("0.02"), gt=0,
        client_data=ClientFieldData(
            prompt=lambda mi: "Enter the stop loss (as a decimal, e.g., 0.02 for 2%): ",
            prompt_on_new=True))
    take_profit: Decimal = Field(
        default=Decimal("0.03"), gt=0,
        client_data=ClientFieldData(
            prompt=lambda mi: "Enter the take profit (as a decimal, e.g., 0.03 for 3%): ",
            prompt_on_new=True))
    time_limit: int = Field(
        default=60 * 45, gt=0,
        client_data=ClientFieldData(
            prompt=lambda mi: "Enter the time limit in seconds (e.g., 2700 for 45 minutes): ",
            prompt_on_new=True))

    trailing_stop_activation_price_delta: Decimal = Field(
        default=Decimal("0.01"), gt=0,
        client_data=ClientFieldData(
            prompt=lambda mi: "Enter the trailing stop activation price (as a decimal, e.g., 0.01 for 1%): ",
            prompt_on_new=True))

    trailing_stop_trailing_delta: Decimal = Field(
        default=Decimal("0.003"), gt=0,
        client_data=ClientFieldData(
            prompt=lambda mi: "Enter the trailing stop trailing delta (as a decimal, e.g., 0.003 for 0.3%): ",
            prompt_on_new=True))

    @property
    def triple_barrier_config(self) -> TripleBarrierConfig:
        return TripleBarrierConfig(
            stop_loss=self.stop_loss,
            take_profit=self.take_profit,
            time_limit=self.time_limit,
            open_order_type=OrderType.MARKET,
            take_profit_order_type=OrderType.LIMIT,
            stop_loss_order_type=OrderType.MARKET,  # Defaulting to MARKET as per requirement
            time_limit_order_type=OrderType.MARKET,  # Defaulting to MARKET as per requirement
            trailing_stop=TrailingStop(
                activation_price=Decimal(self.trailing_stop_activation_price_delta),
                trailing_delta=Decimal(self.trailing_stop_trailing_delta))
        )

    @validator('position_mode', pre=True, allow_reuse=True)
    def validate_position_mode(cls, v: str) -> PositionMode:
        if v.upper() in PositionMode.__members__:
            return PositionMode[v.upper()]
        raise ValueError(f"Invalid position mode: {v}. Valid options are: {', '.join(PositionMode.__members__)}")


class GLKTrailingStop(StrategyV2Base):
    account_config_set = False
    can_enter_position = True
    signal_file = '/tmp/hummingbot_simple_position'
    last_action_timestamp = None

    def __init__(self, connectors: Dict[str, ConnectorBase], config: GLKTrailingStopConfig):
        super().__init__(connectors, config)
        self.config = config
        # trailing_stop = TrailingStop(activation_price=Decimal(self.config.trailing_stop_activation_price_delta),trailing_delta=Decimal(self.config.trailing_stop_trailing_delta))
        # TrailingStop()
        # self.triple_barrier_conf = TripleBarrierConfig(
        #     stop_loss=Decimal(self.config.stop_loss),
        #     take_profit=Decimal(self.config.take_profit),
        #     time_limit=self.config.time_limit,
        #     trailing_stop=trailing_stop,
        #     take_profit_order_type=OrderType.LIMIT,
        #     stop_loss_order_type=OrderType.MARKET,  # Defaulting to MARKET as per requirement
        #     time_limit_order_type=OrderType.MARKET  # Defaulting to MARKET as per requirement
        # )

    def start(self, clock: Clock, timestamp: float) -> None:
        """
        Start the strategy.
        :param clock: Clock to use.
        :param timestamp: Current time.
        """
        self._last_timestamp = timestamp
        self.last_action_timestamp = datetime.now().timestamp()
        self.apply_initial_setting()

    # def on_tick(self):
    #     balance_df = self.get_balance_df()
    #     balance_df.to_csv(path_or_buf="~/tmp/balance.csv")

    def get_signal(self):
        if not os.path.exists(self.signal_file):
            return 0

        # Get modification timestamp of the file
        modification_time = os.path.getmtime(self.signal_file)
        # Compare the timestamps
        if self.last_action_timestamp > modification_time:
            return 0

        # Open the file and read the content
        with open(self.signal_file, 'r') as file:
            content = file.read()
        # Remove newline characters
        content = content.replace('\n', '').replace('\r', '')

        # Check if exists open positions
        open_positions = 0
        for connector_name, trading_pairs in self.config.markets.items():
            for trading_pair in trading_pairs:
                active_longs, active_shorts = self.get_active_executors_by_side(connector_name, trading_pair)
                open_positions += len(active_longs) + len(active_shorts)

        self.logger().info(f"Open positions {open_positions}")

        if open_positions == 0:
            if content == "LONG":
                return 1
            if content == "SHORT":
                return -1

        return 0

    def create_actions_proposal(self) -> List[CreateExecutorAction]:
        create_actions = []

        for connector_name, trading_pairs in self.config.markets.items():
            for trading_pair in trading_pairs:
                signal = self.get_signal()
                active_longs, active_shorts = self.get_active_executors_by_side(connector_name, trading_pair)
                if signal is not None:
                    mid_price = self.market_data_provider.get_price_by_type(connector_name, trading_pair,
                                                                            PriceType.MidPrice)
                    if signal == 1 and len(active_longs) == 0:
                        create_actions.append(CreateExecutorAction(
                            executor_config=PositionExecutorConfig(
                                timestamp=self.current_timestamp,
                                connector_name=connector_name,
                                trading_pair=trading_pair,
                                side=TradeType.BUY,
                                entry_price=mid_price,
                                amount=(self.config.order_amount_quote * self.config.leverage) / mid_price,
                                triple_barrier_config=self.config.triple_barrier_config,
                                leverage=self.config.leverage
                            )))
                        self.last_action_timestamp = datetime.now().timestamp()
                    elif signal == -1 and len(active_shorts) == 0:
                        create_actions.append(CreateExecutorAction(
                            executor_config=PositionExecutorConfig(
                                timestamp=self.current_timestamp,
                                connector_name=connector_name,
                                trading_pair=trading_pair,
                                side=TradeType.SELL,
                                entry_price=mid_price,
                                amount=(self.config.order_amount_quote * self.config.leverage) / mid_price,
                                triple_barrier_config=self.config.triple_barrier_config,
                                leverage=self.config.leverage
                            )))
                        self.last_action_timestamp = datetime.now().timestamp()
        return create_actions

    def stop_actions_proposal(self) -> List[StopExecutorAction]:
        stop_actions = []
        for connector_name, trading_pairs in self.config.markets.items():
            for trading_pair in trading_pairs:
                signal = None
                active_longs, active_shorts = self.get_active_executors_by_side(connector_name, trading_pair)
                if signal is not None:
                    if signal == -1 and len(active_longs) > 0:
                        stop_actions.extend([StopExecutorAction(executor_id=e.id) for e in active_longs])
                    elif signal == 1 and len(active_shorts) > 0:
                        stop_actions.extend([StopExecutorAction(executor_id=e.id) for e in active_shorts])
        return stop_actions

    def get_active_executors_by_side(self, connector_name: str, trading_pair: str):
        active_executors_by_connector_pair = self.filter_executors(
            executors=self.get_all_executors(),
            filter_func=lambda e: e.connector_name == connector_name and e.trading_pair == trading_pair and e.is_active
        )
        active_longs = [e for e in active_executors_by_connector_pair if e.side == TradeType.BUY]
        active_shorts = [e for e in active_executors_by_connector_pair if e.side == TradeType.SELL]
        return active_longs, active_shorts

    def apply_initial_setting(self):
        if not self.account_config_set:
            for connector_name, connector in self.connectors.items():
                if self.is_perpetual(connector_name):
                    connector.set_position_mode(self.config.position_mode)
                    for trading_pair in self.market_data_provider.get_trading_pairs(connector_name):
                        connector.set_leverage(trading_pair, self.config.leverage)
            self.account_config_set = True

    def did_create_buy_order(self, event: BuyOrderCreatedEvent):
        msg = f"GLK Created BUY order {event.type}"
        self.logger().info(msg)

    def did_create_sell_order(self, event: SellOrderCreatedEvent):
        msg = f"GLK Created SELL order {event.type}"
        self.logger().info(msg)

    def did_fill_order(self, event: OrderFilledEvent):
        msg = f"{event.trade_type.name} {event.amount} of {event.trading_pair} at {event.price}"
        Notificator().notify("Order filled", msg)

    # Here I copied the implementation of this method in the parent class but commenting the reference to stop the
    # executor orchestrator because this cause all working orders to be cancelled and, more important, closes the current
    # opens position and worse, in case this positions have been closed manually the executor opens to opposite order creating
    # a new position. With this implementation when stopping the application open orders remains open and working orders
    # remains working
    def on_stop(self):
        # self.executor_orchestrator.stop()
        self.market_data_provider.stop()
        self.listen_to_executor_actions_task.cancel()
        for controller in self.controllers.values():
            controller.stop()
