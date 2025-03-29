import pandas as pd
from sqlalchemy import create_engine

import glk.quant.Data as glkdata
from glk.db.ohlcv import OHLCV


def db_persist(df, trading_pair):
    data_instance = glkdata.Data()
    df2 = data_instance.load_df(df)
    data_instance.prepare_for_db(trading_pair, 'Binance')

    ohlcv = OHLCV()
    ohlcv.create_table(engine, df2)
    ohlcv.save_data(engine, df2)


db_connection_string = "postgresql://lgbkcom:clavicordio68@localhost:5432/glk_gestion_01"
engine = create_engine(db_connection_string)

dframe = pd.read_csv("data/candles_binance_perpetual_XRP-USDT_1d.csv")
db_persist(dframe, "XRP-USDT")

dframe = pd.read_csv("data/candles_binance_perpetual_SOL-USDT_1d.csv")
db_persist(dframe, "SOL-USDT")
