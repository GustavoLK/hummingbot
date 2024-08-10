import os
from typing import Dict, List

import pandas_ta as ta  # noqa: F401
from hummingbot.connector.connector_base import ConnectorBase
from pydantic import Field

from hummingbot.data_feed.candles_feed.candles_factory import CandlesConfig
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase


class GLKBalanceConfig(StrategyV2ConfigBase):
    script_file_name: str = Field(default_factory=lambda: os.path.basename(__file__))
    candles_config: List[CandlesConfig] = []


class GLKBalance(StrategyV2Base):

    def __init__(self, connectors: Dict[str, ConnectorBase], config: GLKBalanceConfig):
        super().__init__(connectors, config)
        self.config = config


    def on_tick(self):
        balance_df = self.get_balance_df()
        balance_df.to_csv(path_or_buf="data/glk-balance.csv")
