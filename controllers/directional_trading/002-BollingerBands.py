import os
from decimal import Decimal
from typing import List

import pandas_ta as ta  # noqa: F401
from pydantic import Field, validator

from hummingbot.client.config.config_data_types import ClientFieldData
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.strategy_v2.controllers.directional_trading_controller_base import (
    DirectionalTradingControllerBase,
    DirectionalTradingControllerConfigBase,
)
from hummingbot.strategy_v2.executors.position_executor.data_types import PositionExecutorConfig, TripleBarrierConfig


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
        bbp = df[f"BBP_{self.config.bb_length}_{self.config.bb_std}"]
        df['TP'] = df[f"BBB_{self.config.bb_length}_{self.config.bb_std}"] / 200
        tp = df['TP']

        # I delete some columns I don't use in order to clarify status in the GUI
        columns_to_drop = [col for col in df.columns if col.startswith(('BBM', 'BBL', 'BBU', 'BBB'))]
        df.drop(columns=columns_to_drop, inplace=True)

        long_condition = (bbp.shift(2) <= 0) & (bbp.shift(1) > 0) & (tp > 0.001)
        short_condition = (bbp.shift(2) >= 1.0) & (bbp.shift(1) < 1.0) & (tp > 0.001)

        df["signal"] = 0
        # df.loc[long_condition, "signal"] = 1
        df.loc[short_condition, "signal"] = -1

        # Update processed data
        self.processed_data["signal"] = df["signal"].iloc[-1]
        self.processed_data["features"] = df

    # Esto esta en depuracion. No estaria andando como es debido. No setea el valor del TP
    # def get_executor_config(self, trade_type: TradeType, price: Decimal, amount: Decimal):
    #     """
    #     Get the executor config based on the trade_type, price and amount. This method can be overridden by the
    #     subclasses if required.
    #     """
    #
    #     tp = self.processed_data["features"]['TP'].iloc[-1]
    #     if not isinstance(tp, Decimal):
    #         tp = Decimal(str(tp))
    #
    #     tbc = TripleBarrierConfig(
    #         stop_loss=None,
    #         take_profit=tp,
    #         time_limit=None,
    #         trailing_stop=None,
    #         open_order_type=OrderType.LIMIT,
    #         take_profit_order_type=OrderType.MARKET,
    #         stop_loss_order_type=OrderType.MARKET,
    #         time_limit_order_type=OrderType.MARKET
    #     )
    #
    #     tbc = self.config.triple_barrier_config
    #
    #     self.logger().info(f"Entry with TAKE PROFIT of {tbc.take_profit}")
    #
    #     return PositionExecutorConfig(
    #         timestamp=self.market_data_provider.time(),
    #         connector_name=self.config.connector_name,
    #         trading_pair=self.config.trading_pair,
    #         side=trade_type,
    #         entry_price=price,
    #         amount=amount,
    #         triple_barrier_config=tbc,
    #         leverage=self.config.leverage,
    #     )
