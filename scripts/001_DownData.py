import os
from typing import Dict

from sqlalchemy import create_engine

import glk.quant.Data as glkdata
from glk.db.ohlcv import OHLCV
from hummingbot import data_path
from hummingbot.client.hummingbot_application import HummingbotApplication
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.connector.exchange.binance.binance_api_order_book_data_source import BinanceAPIOrderBookDataSource
from hummingbot.data_feed.candles_feed.candles_factory import CandlesConfig, CandlesFactory
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase


class DownloadCandles(ScriptStrategyBase):

    exchange = os.getenv("EXCHANGE", "binance_perpetual")
    trading_pairs = os.getenv("TRADING_PAIRS", "BTC-USDT,ETH-USDT,XRP-USDT,BNB-USDT,SOL-USDT").split(",")
    # intervals = os.getenv("INTERVALS", "1d").split(",")
    # days_to_download = list(map(int, os.getenv("DAYS_TO_DOWNLOAD", "1460").split(",")))
    # trading_pairs = os.getenv("TRADING_PAIRS", "BTC-USDT").split(",")
    intervals = os.getenv("INTERVALS", "1m").split(",")
    days_to_download = list(map(int, os.getenv("DAYS_TO_DOWNLOAD", "90").split(",")))

    # we can initialize any trading pair since we only need the candles
    markets = {"binance_paper_trade": {"BTC-USDT"}}

    engine = create_engine("postgresql://lgbkcom:clavicordio68@localhost:5432/glk_gestion_01")


    @staticmethod
    def get_max_records(days_to_download: int, interval: str) -> int:
        conversion = {"s": 1 / 60, "m": 1, "h": 60, "d": 1440}
        unit = interval[-1]
        quantity = int(interval[:-1])
        return int(days_to_download * 24 * 60 / (quantity * conversion[unit]))

    def __init__(self, connectors: Dict[str, ConnectorBase]):
        super().__init__(connectors)

        data_source = BinanceAPIOrderBookDataSource(trading_pairs=[])


        download_dict = dict(zip(self.intervals, self.days_to_download))
        combinations = [(trading_pair, interval) for trading_pair in self.trading_pairs for interval in self.intervals]

        self.candles = {f"{combinations[0]}_{combinations[1]}": {} for combinations in combinations}
        # we need to initialize the candles for each trading pair
        for combination in combinations:
            self.logger().info(f"Max records for {combination[0]}_{combination[1]}: {self.get_max_records(download_dict[combination[1]], combination[1])}")
            candle = CandlesFactory.get_candle(CandlesConfig(connector=self.exchange, trading_pair=combination[0], interval=combination[1], max_records=self.get_max_records(download_dict[combination[1]], combination[1])))
            candle.start()
            # we are storing the candles object and the csv path to save the candles
            self.candles[f"{combination[0]}_{combination[1]}"]["candles"] = candle
            self.candles[f"{combination[0]}_{combination[1]}"][
                "csv_path"] = data_path() + f"/candles_{self.exchange}_{combination[0]}_{combination[1]}.csv"

    def on_tick(self):
        self.logger().info("On Tick")
        for trading_pair, candles_info in self.candles.items():
            if not candles_info["candles"].ready:
                self.logger().info(f"Candles not ready yet for {trading_pair}! Missing {candles_info['candles']._candles.maxlen - len(candles_info['candles']._candles)}")
                pass
            else:
                df = candles_info["candles"].candles_df
                # df.to_csv(candles_info["csv_path"], index=False)
                self.logger().info("BEGIN Persist")
                self.db_persist(df, trading_pair)
                self.logger().info("END Persist")
                self.logger().info(f"Candles READY for {trading_pair}")
        if all(candles_info["candles"].ready for candles_info in self.candles.values()):
            self.logger().info("All candles READY. Going to STOP App")
            HummingbotApplication.main_application().stop()

    def on_stop(self):
        for candles_info in self.candles.values():
            candles_info["candles"].stop()


    def db_persist(self, df, trading_pair):
        data_instance = glkdata.Data()
        df2 = data_instance.load_df(df)
        data_instance.prepare_for_db(trading_pair.rsplit('_', 1)[0], 'Binance')

        ohlcv = OHLCV()
        ohlcv.create_table(self.engine, df2)
        ohlcv.save_data(self.engine, df2)

