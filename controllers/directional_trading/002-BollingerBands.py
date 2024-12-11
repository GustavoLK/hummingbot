import os
import time
from decimal import Decimal
from typing import List

import numpy as np
import pandas_ta as ta  # noqa: F401
from hummingbot.strategy_v2.models.executors import CloseType
from pydantic import Field, validator

from hummingbot.client.config.config_data_types import ClientFieldData
from hummingbot.core.data_type.common import OrderType, TradeType, PositionSide
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.strategy_v2.controllers.directional_trading_controller_base import (
    DirectionalTradingControllerBase,
    DirectionalTradingControllerConfigBase,
)
from hummingbot.strategy_v2.executors.position_executor.data_types import PositionExecutorConfig, TripleBarrierConfig
from glk.Notificator import Notificator


# Esto funciona hasta ahi, no anda lo de setear el take profit de cada orden. Las ordenes dan error en  Hyperliquid

class BollingerBandsControllerConfig(DirectionalTradingControllerConfigBase):
    controller_name = os.path.splitext(os.path.basename(os.path.abspath(__file__)))[0]
    candles_config: List[CandlesConfig] = []
    candles_connector: str = Field(
        default=None,
        client_data=ClientFieldData(
            prompt_on_new=True,
            prompt=lambda
                mi: "Enter the connector for the candles data, leave empty to use the same exchange as the connector: ", )
    )
    candles_trading_pair: str = Field(
        default=None,
        client_data=ClientFieldData(
            prompt_on_new=True,
            prompt=lambda
                mi: "Enter the trading pair for the candles data, leave empty to use the same trading pair as the connector: ", )
    )
    interval: str = Field(
        default="1m",
        client_data=ClientFieldData(
            prompt=lambda mi: "Enter the candle interval (e.g., 1m, 5m, 1h, 1d): ",
            prompt_on_new=False))
    bb_length: int = Field(
        default=20,
        client_data=ClientFieldData(
            prompt=lambda mi: "Enter the Bollinger Bands length: ",
            prompt_on_new=True))
    bb_std: float = Field(
        default=2.0,
        client_data=ClientFieldData(
            prompt=lambda mi: "Enter the Bollinger Bands standard deviation: ",
            prompt_on_new=False))

    position_side: PositionSide = Field(
        default="LONG",
        client_data=ClientFieldData(
            prompt=lambda mi: "Enter the position side (LONG/SHORT/BOTH): ",
            prompt_on_new=True
        ))

    @validator("candles_connector", pre=True, always=True)
    def set_candles_connector(cls, v, values):
        if v is None or v == "":
            return values.get("connector_name")
        return v

    @validator("candles_trading_pair", pre=True, always=True)
    def set_candles_trading_pair(cls, v, values):
        if v is None or v == "":
            return values.get("trading_pair")
        return v


class BollingerBandsController(DirectionalTradingControllerBase):

    last_middle_cross = None

    def __init__(self, config: BollingerBandsControllerConfig, *args, **kwargs):
        self.config = config
        self.max_records = self.config.bb_length + 5
        if len(self.config.candles_config) == 0:
            self.config.candles_config = [CandlesConfig(
                connector=config.candles_connector,
                trading_pair=config.candles_trading_pair,
                interval=config.interval,
                max_records=self.max_records
            )]
        super().__init__(config, *args, **kwargs)

    async def update_processed_data(self):
        df = self.market_data_provider.get_candles_df(connector_name=self.config.candles_connector,
                                                      trading_pair=self.config.candles_trading_pair,
                                                      interval=self.config.interval,
                                                      max_records=self.max_records)

        df.ta.bbands(length=self.config.bb_length, std=self.config.bb_std, append=True)
        str_band = f"{self.config.bb_length}_{self.config.bb_std}"
        bbp = df[f"BBP_{str_band}"]
        # df['TP'] = df[f"BBB_{str_band}"] / 200
        df['TP'] = np.where(
            df['close'] > df[f"BBM_{str_band}"],
            (df[f"BBU_{str_band}"] - df[f"BBM_{str_band}"]) / df[f"BBU_{str_band}"],
            (df[f"BBM_{str_band}"] - df[f"BBL_{str_band}"]) / df[f"BBL_{str_band}"]
        )
        tp = df['TP']

        # I delete some columns I don't use in order to clarify status in the GUI
        columns_to_drop = [col for col in df.columns if col.startswith(('BBM', 'BBL', 'BBU', 'BBB'))]
        df.drop(columns=columns_to_drop, inplace=True)


        last_position = self.get_last_position()

        can_trade_long = True
        if last_position is not None and last_position.side == TradeType.BUY \
                and last_position.close_type == CloseType.STOP_LOSS \
                and (self.last_middle_cross is None or self.last_middle_cross < last_position.close_timestamp):
            can_trade_long = False

        can_trade_short = True
        if last_position is not None and last_position.side == TradeType.SELL \
                and last_position.close_type == CloseType.STOP_LOSS \
                and (self.last_middle_cross is None or self.last_middle_cross < last_position.close_timestamp):
            can_trade_short = False

        up_limit = 1.0
        long_condition = (bbp.shift(2) <= 0) & (bbp.shift(1) > 0) & (tp > 0.001) & can_trade_long
        short_condition = (bbp.shift(2) >= up_limit) & (bbp.shift(1) < up_limit) & (tp > 0.001) & can_trade_short
        middle_crossing = (bbp.shift(2) > 0.5) & (bbp.shift(1) < 0.5) | (bbp.shift(2) < 0.5) & (bbp.shift(1) > 0.5)
        df['Cross'] = middle_crossing

        df["signal"] = 0
        if self.config.position_side == PositionSide.LONG or self.config.position_side == PositionSide.BOTH:
            df.loc[long_condition, "signal"] = 1
        if self.config.position_side == PositionSide.SHORT or self.config.position_side == PositionSide.BOTH:
            df.loc[short_condition, "signal"] = -1


        if df["Cross"].iloc[-1]:
            self.last_middle_cross = time.time()
            # Notificator().notify("Aviso", "Mdidle line cross")

        # Update processed data
        self.processed_data["signal"] = df["signal"].iloc[-1]
        self.processed_data["features"] = df

    # Esto esta en depuracion. No estaria andando como es debido. No setea el valor del TP
    def get_executor_config(self, trade_type: TradeType, price: Decimal, amount: Decimal):
        """
        Get the executor config based on the trade_type, price and amount. This method can be overridden by the
        subclasses if required.
        """
        # Notificator().notify("Aviso", "In get_executor_config")

        tp = self.processed_data["features"]['TP'].iloc[-1]
        if not isinstance(tp, Decimal):
            tp = Decimal(str(tp))

        tbc = TripleBarrierConfig(
            stop_loss=None,
            take_profit=tp,
            time_limit=None,
            trailing_stop=None,
            open_order_type=OrderType.MARKET,
            take_profit_order_type=OrderType.LIMIT,
            stop_loss_order_type=OrderType.MARKET,
            time_limit_order_type=OrderType.MARKET
        )

        # tbc = self.config.triple_barrier_config

        self.logger().info(f"GLKLOG Entry with TAKE PROFIT of {tbc.take_profit} and Price {price}")

        return PositionExecutorConfig(
            timestamp=self.market_data_provider.time(),
            connector_name=self.config.connector_name,
            trading_pair=self.config.trading_pair,
            side=trade_type,
            entry_price=price,
            amount=amount,
            triple_barrier_config=tbc,
            leverage=self.config.leverage,
        )

    def can_create_executor(self, signal: int) -> bool:
        """
        Check if an executor can be created based on the signal, the quantity of active executors and the cooldown time.
        """
        active_executors_by_signal_side = self.filter_executors(
            executors=self.executors_info,
            filter_func=lambda x: x.is_active and (x.side == TradeType.BUY if signal > 0 else TradeType.SELL))
        max_timestamp = max([executor.timestamp for executor in active_executors_by_signal_side], default=0)
        active_executors_condition = len(active_executors_by_signal_side) < self.config.max_executors_per_side
        cooldown_condition = self.market_data_provider.time() - max_timestamp > self.config.cooldown_time
        return active_executors_condition and cooldown_condition


    def get_last_position(self):
        finished_executors = active_executors_by_signal_side = self.filter_executors(
            executors=self.executors_info,
            filter_func=lambda x: not x.is_active and x.is_done and x.close_timestamp)
        if len(finished_executors) == 0:
            return None
        finished_executors.sort(key=lambda x: x.close_timestamp)
        return finished_executors[-1]



