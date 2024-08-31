import os
from typing import List

import pandas_ta as ta  # noqa: F401
from pydantic import Field, validator

from hummingbot.client.config.config_data_types import ClientFieldData
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.strategy_v2.controllers.directional_trading_controller_base import (
    DirectionalTradingControllerBase,
    DirectionalTradingControllerConfigBase,
)


class MovingAvgCrossControllerConfig(DirectionalTradingControllerConfigBase):
    # controller_name = "001-MovingAvgCross"
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
    fast_sma_length: int = Field(
        default=1,
        client_data=ClientFieldData(
            prompt=lambda mi: "Enter the Short Moving Average length: ",
            prompt_on_new=True))
    slow_sma_length: int = Field(
        default=50,
        client_data=ClientFieldData(
            prompt=lambda mi: "Enter the Long Moving Average length: ",
            prompt_on_new=True))

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


class MovingAvgCrossController(DirectionalTradingControllerBase):

    def __init__(self, config: MovingAvgCrossControllerConfig, *args, **kwargs):
        self.config = config
        self.max_records = max(config.fast_sma_length, config.slow_sma_length) + 10
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
        # Add indicators

        df.ta.sma(length=self.config.fast_sma_length, append=True)
        df.ta.sma(length=self.config.slow_sma_length, append=True)

        fast = df[f"SMA_{self.config.fast_sma_length}"]
        slow = df[f"SMA_{self.config.slow_sma_length}"]

        long_condition = (fast > slow) & (fast.shift(1) <= slow.shift(1))
        short_condition = (fast < slow) & (fast.shift(1) >= slow.shift(1))

        # Generate signal
        # long_condition = df.ta.cross(df[f"SMA_{self.config.fast_sma_length}"], df[f"SMA_{self.config.slow_sma_length}"])
        # short_condition = df.ta.cross(df[f"SMA_{self.config.fast_sma_length}"],
        #                               df[f"SMA_{self.config.slow_sma_length}"], above=False)

        df["signal"] = 0
        df.loc[long_condition, "signal"] = 1
        df.loc[short_condition, "signal"] = -1

        # Update processed data
        self.processed_data["signal"] = df["signal"].iloc[-1]
        self.processed_data["features"] = df
