import os
from typing import List

import pandas_ta as ta  # noqa: F401
from pydantic import Field

from hummingbot.data_feed.candles_feed.candles_factory import CandlesConfig
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
from hummingbot.strategy.strategy_v2_base import StrategyV2ConfigBase


class GLKBalanceConfig(StrategyV2ConfigBase):
    script_file_name: str = Field(default_factory=lambda: os.path.basename(__file__))
    candles_config: List[CandlesConfig] = []


class GLKBalance(ScriptStrategyBase):
    markets = {
        "binance_perpetual": {"BTC-USDT", "ETH-USDT", "BNB-USDT"}
    }

    def on_tick(self):
        balance_df = self.get_balance_df()
        balance_df.to_csv(path_or_buf="data/glk-balance.csv")
