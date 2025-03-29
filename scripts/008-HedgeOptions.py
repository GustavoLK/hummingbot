import os
from datetime import datetime
from decimal import Decimal
from enum import Enum, auto
from typing import Dict, List, Optional

import pandas as pd
import yaml
from pydantic import Field

from glk.Notificator import Notificator
from hummingbot.client.config.config_data_types import ClientFieldData
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import OrderType, PositionMode, PriceType, TradeType
from hummingbot.core.event.events import OrderFilledEvent
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.strategy.strategy_py_base import BuyOrderCompletedEvent, SellOrderCompletedEvent
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, StopExecutorAction

if os.environ.get('PYTHONDEV') == '1':
    try:
        import debugpy
        debugpy.listen(("0.0.0.0", 5678))
        debugpy.wait_for_client()
    except RuntimeError as e:
        if "listen() has already been called" in str(e):
            print("Debugpy is already listening, continuing...")
        else:
            raise  # Re-raise if it's a different RuntimeError


class HedgingStatus(Enum):
    WAITING = auto()
    OPENING_LONG = auto()
    OPENED_LONG = auto()
    OPENING_SHORT = auto()
    OPENED_SHORT = auto()
    CLOSING_LONG = auto()
    CLOSED_LONG = auto()
    CLOSING_SHORT = auto()
    CLOSED_SHORT = auto()
    STOPPED = auto()


class HedgingAction(Enum):
    OPEN_LONG = auto()
    CLOSE_LONG = auto()
    OPEN_SHORT = auto()
    CLOSE_SHORT = auto()


class GLKHedgeOptionsConfig(StrategyV2ConfigBase):
    script_file_name: str = Field(default_factory=lambda: os.path.basename(__file__))
    candles_config: List[CandlesConfig] = []
    conf_script: str = Field(
        client_data=ClientFieldData(
            prompt=lambda mi: "Enter the script with the configuration: ",
            prompt_on_new=True))


class GLKHedgeOptions(StrategyV2Base):
    pair = None
    conf_file = None
    config_readed = None
    last_conf_timestamp = None
    total_pnl = 0.00
    last_df_index = None
    df_file = None
    max_price = Decimal(0.00)
    min_price = Decimal(1_000_000)
    taker_fee = Decimal(0.000336)  # HARDCODED. Taker commision on Hyperliquid

    # HARCODED. En caso de varios trades ganadores que acumulen una ganacia > gain_sl_trigger se setea un SL con este porcentaje de ganancias acumuladas
    # TODO parametrizar de archivo
    gain_sl_trigger = 20
    gaining_sl = 0.3 


    def __init__(self, connectors: Dict[str, ConnectorBase], config: Optional[GLKHedgeOptionsConfig] = None):
        super().__init__(connectors, config)
        market = list(self.markets.values())[0]
        self.pair = list(market)[0]


        # hb_app = HummingbotApplication.main_application()
        # self.conf_file = f"conf/scripts/{hb_app.strategy_file_name}"

        self.config = config
        self.conf_file = f"conf/scripts/{config.conf_script}"
        self.df = pd.DataFrame(columns=[
            'Ticker',
            'Amount',
            'Actual price',
            'Entry price',
            'Exit price',
            'Entry time',
            'Exit time',
            'Side',
            'Fees',
            'PnL',
            'PnL%'
        ])

        # Optional: Set data types for the columns
        self.df = self.df.astype({
            'Ticker': 'object',
            'Amount': 'float64',
            'Actual price': 'float64',
            'Entry price': 'float64',
            'Exit price': 'float64',
            'Entry time': 'datetime64[ns]',
            'Exit time': 'datetime64[ns]',
            'Side': 'object',
            'Fees': 'float64',
            'PnL': 'float64',
            'PnL%': 'float64'
        })

        self.df_file = f"data/HedgeOptionsV2-{self.pair}-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".csv"



    def read_file(self):
        if not os.path.exists(self.conf_file):
            return False

        if self.last_conf_timestamp is not None:
            modification_time = os.path.getmtime(self.conf_file)
            # Compare the timestamps
            if self.last_conf_timestamp > modification_time:
                return False

        with open(self.conf_file, 'r') as file:
            try:
                previous_leverage = None
                if self.config_readed is not None:
                    previous_leverage = self.config_readed['leverage']
                self.config_readed = yaml.safe_load(file)
                # Convert the string readed into a HedgingStatus
                self.config_readed['status'] = HedgingStatus[self.config_readed['status']]
                self.logger().info(f"Configuration file (re)readed")
                self.last_conf_timestamp = datetime.now().timestamp()


                if "entry" not in self.config_readed['BUY']:
                    self.config_readed['BUY']['entry'] = self.config_readed['BUY']['entry_orig']
                if "entry" not in self.config_readed['SELL']:
                    self.config_readed['SELL']['entry'] = self.config_readed['SELL']['entry_orig']

                # If SL is not set in price then is calculated based in percentaje
                if "sl" not in self.config_readed['BUY']:
                    self.config_readed['BUY']['sl'] = self.config_readed['BUY']['entry'] * (1 - self.config_readed['BUY']['sl_pct'])
                if "sl" not in self.config_readed['SELL']:
                    self.config_readed['SELL']['sl'] = self.config_readed['SELL']['entry'] * (1 + self.config_readed['SELL']['sl_pct'])

                if "entry_price" in self.config_readed and "amount" in self.config_readed and len(self.df) == 0:
                    action = "BUY"
                    if self.config_readed['status'] == HedgingStatus.OPENED_SHORT:
                        action = "SELL"
                    self._add_trade_to_df(action, Decimal(self.config_readed['amount']), self.pair, Decimal(self.config_readed['entry_price']))

                # Si actualizo el archivo de configuracion reseteo los status
                # self.positions['Pair-01']['status'] = HedgingStatus.WAITING
                # self.positions['Pair-02']['status'] = HedgingStatus.WAITING

                if previous_leverage is None or previous_leverage != self.config_readed['leverage']:
                    self.set_leverage('', self.pair, self.config_readed['leverage'])

                return True

            except yaml.YAMLError as e:
                print(f"Error reading YAML file: {e}")
                return False

    def write_file(self, allow_reread: bool):
        """
        Writes the current configuration from self.config_readed back to the YAML file.
        Returns True if successful, False otherwise.
        """
        if self.config_readed is None:
            self.logger().error("No configuration data to write")
            return False

        try:
            # Create a copy of the config to modify for saving
            config_to_save = self.config_readed.copy()

            # Convert HedgingStatus enum back to string for YAML serialization
            config_to_save['status'] = config_to_save['status'].name

            # Write to the file
            with open(self.conf_file, 'w') as file:
                yaml.safe_dump(config_to_save, file, default_flow_style=False)

            # Update the timestamp after successful write
            if not allow_reread:
                self.last_conf_timestamp = datetime.now().timestamp()
            self.logger().info(f"Configuration file written successfully. Reread: {allow_reread}")
            return True

        except (yaml.YAMLError, IOError) as e:
            self.logger().error(f"Error writing YAML file: {e}")
            return False

    def set_leverage(self, connector: str, trading_pair: str, leverage: int):
        connector_name = connector
        if connector_name == '':
            connector_name = list(self.config.markets.keys())[0]
        perp_connector = self.connectors[connector_name]
        perp_connector.set_position_mode(PositionMode.ONEWAY)

        perp_connector.set_leverage(trading_pair=trading_pair, leverage=leverage)
        self.logger().info(f"Setting leverage to {leverage}x for {connector_name} on {trading_pair}")

    def on_tick(self):
        # TODO emprolijar esto, tiene hardcodeado el pair y el exchange
        # candles_df = self.market_data_provider.get_candles_df('binance_perpetual', 'ETH-USDT', interval="1m", max_records=120)


        self.read_file()
        self.process_pair()
        self.update_trade_in_df()

        if len(self.df) > 0:
            self.df.to_csv(self.df_file)

        # if self.positions['Pair-01']['status'] == HedgingStatus.STOPPED and self.positions['Pair-02']['status'] == HedgingStatus.STOPPED:
        #     self.logger().info(f"Stopping application")
        #     HummingbotApplication.main_application().stop()

    def create_actions_proposal(self) -> List[CreateExecutorAction]:
        create_actions = []
        return create_actions

    def process_pair(self):
        price = self.market_data_provider.get_price_by_type('hyperliquid_perpetual', self.pair, PriceType.MidPrice)

        current_time = datetime.now()
        if current_time > self.config_readed['BUY']['not_after'] and current_time > self.config_readed['SELL']['not_after']:
            if self.config_readed['status'] == HedgingStatus.WAITING:
                self.config_readed['status'] = HedgingStatus.STOPPED
                return

        if self.config_readed['status'] == HedgingStatus.WAITING:
            if current_time < self.config_readed['BUY']['not_after'] and price > self.config_readed['BUY']['entry']:
                self.operate_pair(HedgingAction.OPEN_LONG)
                return
            if current_time < self.config_readed['SELL']['not_after'] and price < self.config_readed['SELL']['entry']:
                self.operate_pair(HedgingAction.OPEN_SHORT)
                return

        self.adjust_entry(price)
        if self.config_readed['status'] == HedgingStatus.OPENED_LONG:
            if price < self.config_readed['BUY']['sl']:
                self.logger().info(f"LONG STOP LOSS {price} < {self.config_readed['BUY']['sl']}")
                self.operate_pair(HedgingAction.CLOSE_LONG)
                return
            else:
                self.adjust_prices(price)
        elif self.config_readed['status'] == HedgingStatus.OPENED_SHORT:
            if price > self.config_readed['SELL']['sl']:
                self.logger().info(f"SHORT STOP LOSS {price} > {self.config_readed['SELL']['sl']}")
                self.operate_pair(HedgingAction.CLOSE_SHORT)
                return
            else:
                self.adjust_prices(price)


    def adjust_prices(self, price):
        price_diff = price - Decimal(self.config_readed['entry_price'])
        ts_activation_pct = Decimal(self.config_readed['BUY']['ts_activation'])
        if self.config_readed['status'] == HedgingStatus.OPENED_SHORT:
            price_diff = -1 * price_diff
            ts_activation_pct = Decimal(self.config_readed['SELL']['ts_activation'])

        price_diff_pct = price_diff / Decimal(self.config_readed['entry_price'])
        if price_diff_pct > ts_activation_pct:
            if self.config_readed['status'] == HedgingStatus.OPENED_LONG:
                if price > self.max_price:
                    self.max_price = price
                    entry_orig = self.config_readed['BUY']['entry']
                    sl_orig = self.config_readed['BUY']['sl']
                    self.config_readed['BUY']['sl'] = round(price * (1 - Decimal(self.config_readed['BUY']['ts'])), 2)
                    self.config_readed['BUY']['entry'] = self.config_readed['BUY']['sl'] * (1 + ts_activation_pct)
                    self.logger().info(f"LONG Adjusting. Price {price} SL {sl_orig} -> {self.config_readed['BUY']['sl']}  Entry {entry_orig} -> {self.config_readed['BUY']['entry']}")
                    self.write_file(True)
            elif self.config_readed['status'] == HedgingStatus.OPENED_SHORT:
                if price < self.min_price:
                    self.min_price = price
                    entry_orig = self.config_readed['SELL']['entry']
                    sl_orig = self.config_readed['SELL']['sl']
                    self.config_readed['SELL']['sl'] = round(price * (1 + Decimal(self.config_readed['SELL']['ts'])), 2)
                    self.config_readed['SELL']['entry'] = self.config_readed['SELL']['sl'] * (1 - ts_activation_pct)
                    self.logger().info(f"SHORT Adjusting. Price {price} SL {sl_orig} -> {self.config_readed['SELL']['sl']}  Entry {entry_orig} -> {self.config_readed['SELL']['entry']}")
                    self.write_file(True)
        else:
            long_pnl = float(self.df[(self.df['Ticker'] == self.pair) & (self.df['Side'] == 'LONG') & (self.df['Exit price'].notna())]['PnL'].sum())
            short_pnl = float(self.df[(self.df['Ticker'] == self.pair) & (self.df['Side'] == 'SHORT') & (self.df['Exit price'].notna())]['PnL'].sum())
            if self.config_readed['status'] == HedgingStatus.OPENED_LONG and long_pnl > self.gain_sl_trigger:
                sl_orig = round(self.config_readed['BUY']['sl'], 2)
                new_sl = sl_orig
                pnl_sl_points = (long_pnl *  self.gaining_sl / self.config_readed['BUY']['order_amount'])
                small_sl = self.config_readed['entry_price'] - pnl_sl_points
                # El SL es el original partiendo del precio de entrada pero si estoy en zona de tradeo ( por arriba de la entrada )
                # y ya tengo ganancias entonces uso el SL antes calculado ( me dara un perdidad pero conservare parte de las ganacias )
                if price > self.config_readed['BUY']['entry_orig'] and small_sl > sl_orig:
                    new_sl = round(small_sl, 2)
                if abs(new_sl - sl_orig) > 0.0001:
                    self.logger().info(f"LONG Adjusting SMALL STOP LOSS. Price: {price} Pnl: {long_pnl} SL points: {pnl_sl_points} SL: {sl_orig} -> {new_sl}")
                    self.config_readed['BUY']['sl'] = new_sl
                    self.write_file(True)
            elif self.config_readed['status'] == HedgingStatus.OPENED_SHORT and short_pnl > self.gain_sl_trigger:
                sl_orig = round(self.config_readed['SELL']['sl'], 2)
                new_sl = sl_orig
                pnl_sl_points = (short_pnl * self.gaining_sl / self.config_readed['SELL']['order_amount'])
                small_sl = self.config_readed['entry_price'] + pnl_sl_points
                # El SL es el original partiendo del precio de entrada pero si estoy en zona de tradeo ( por arriba de la entrada )
                # y ya tengo ganancias entonces uso el SL antes calculado ( me dara un perdidad pero conservare parte de las ganacias )
                if price < self.config_readed['SELL']['entry_orig'] and small_sl < sl_orig:
                    new_sl = round(small_sl, 2)
                if abs(new_sl - sl_orig) > 0.0001:
                    self.logger().info(f"SHORT Adjusting SMALL STOP LOSS. Price: {price} Pnl: {short_pnl} SL points: {pnl_sl_points} SL: {sl_orig} -> {new_sl}")
                    self.config_readed['SELL']['sl'] = new_sl
                    self.write_file(True)

    def adjust_entry(self, price):
        if self.config_readed['status'] != HedgingStatus.WAITING:
            return
        if len(self.df) == 0:
            return

        last_trade = self.df.iloc[-1]
        if last_trade['Side'] == "LONG":
            ts_activation_price = last_trade['Exit price'] * (1 - Decimal(self.config_readed['BUY']['ts_activation']))
            if self.config_readed['BUY']['entry'] > self.config_readed['BUY']['entry_orig'] and self.config_readed['BUY']['entry_orig'] < ts_activation_price:
                ts_price = price * (1 + Decimal(self.config_readed['BUY']['ts']))
                if ts_price < self.config_readed['BUY']['entry']:
                    entry_orig = self.config_readed['BUY']['entry']
                    sl_orig = self.config_readed['BUY']['sl']

                    self.config_readed['BUY']['entry'] = ts_price                                        
                    self.config_readed['BUY']['sl'] = round(self.config_readed['BUY']['entry'] * (1 - Decimal(self.config_readed['BUY']['sl_pct'])), 2)
                    
                    self.logger().info(f"WAITING Adjusting LONG ENTRY. Price {price} Entry: {entry_orig} -> {self.config_readed['BUY']['entry']}  SL: {sl_orig} -> {self.config_readed['BUY']['sl']}")                    
                    self.write_file(True)
        elif last_trade['Side'] == "SHORT":
            ts_activation_price = last_trade['Exit price'] * (1 + Decimal(self.config_readed['SELL']['ts_activation']))
            if self.config_readed['SELL']['entry'] < self.config_readed['SELL']['entry_orig'] and self.config_readed['SELL']['entry_orig'] > ts_activation_price:
                ts_price = price * (1 - Decimal(self.config_readed['SELL']['ts']))
                if ts_price > self.config_readed['SELL']['entry']:
                    entry_orig = self.config_readed['SELL']['entry']
                    sl_orig = self.config_readed['SELL']['sl']

                    self.config_readed['SELL']['entry'] = ts_price                    
                    self.config_readed['SELL']['sl'] = round(self.config_readed['SELL']['entry'] * (1 + Decimal(self.config_readed['SELL']['sl_pct'])), 2)

                    self.logger().info(f"WAITING Adjusting SHORT ENTRY. Price {price} Entry: {entry_orig} -> {self.config_readed['SELL']['entry']}  SL: {sl_orig} -> {self.config_readed['SELL']['sl']}")                                        
                    self.write_file(True)



    def operate_pair(self, action: HedgingAction):
        price = Decimal(self.market_data_provider.get_price_by_type('hyperliquid_perpetual', self.pair, PriceType.MidPrice))
        next_action = HedgingStatus.STOPPED
        if action == HedgingAction.OPEN_LONG:
            next_action = HedgingStatus.OPENING_LONG
        elif action == HedgingAction.OPEN_SHORT:
            next_action = HedgingStatus.OPENING_SHORT
        elif action == HedgingAction.CLOSE_LONG:
            next_action = HedgingStatus.CLOSING_LONG
        elif action == HedgingAction.CLOSE_SHORT:
            next_action = HedgingStatus.CLOSING_SHORT

        if action == HedgingAction.OPEN_LONG or action == HedgingAction.CLOSE_SHORT:
            amount = Decimal(self.config_readed['BUY']['order_amount'])
            if action == HedgingAction.CLOSE_SHORT:
                amount = Decimal(self.config_readed['SELL']['order_amount'])

            self.config_readed['status'] = next_action
            if self.config_readed['dry_run']:
                self._did_fill_order("BUY", amount, self.pair, price)
            else:
                self.buy(
                    connector_name='hyperliquid_perpetual',
                    trading_pair=self.pair,
                    amount=amount,
                    order_type=OrderType.MARKET,
                    price=price
                )
        elif action == HedgingAction.OPEN_SHORT or action == HedgingAction.CLOSE_LONG:
            amount = Decimal(self.config_readed['SELL']['order_amount'])
            if action == HedgingAction.CLOSE_LONG:
                amount = Decimal(self.config_readed['BUY']['order_amount'])

            self.config_readed['status'] = next_action
            if self.config_readed['dry_run']:
                self._did_fill_order("SELL", amount, self.pair, price)
            else:
                self.sell(
                    connector_name='hyperliquid_perpetual',
                    trading_pair=self.pair,
                    amount=amount,
                    order_type=OrderType.MARKET,
                    price=price
                )





    def stop_actions_proposal(self) -> List[StopExecutorAction]:
        stop_actions = []
        return stop_actions


    def did_fill_order(self, event: OrderFilledEvent):
        return self._did_fill_order(event.trade_type, event.amount, event.trading_pair, event.price)


    # def did_complete_buy_order(self, event: BuyOrderCompletedEvent):
    #     return self._did_fill_order("BUY", event.base_asset_amount, self.pair, event.quote_asset_amount)
    #
    # def did_complete_sell_order(self, event: SellOrderCompletedEvent):
    #     return self._did_fill_order("SELL", event.base_asset_amount, self.pair, event.quote_asset_amount)


    def _did_fill_order(self, trade_type, amount, trading_pair, price):
        msg = f"{trade_type} {amount} of {trading_pair} at {price} Dry run: {self.config_readed['dry_run']}"

        if self.config_readed['status'] == HedgingStatus.OPENING_LONG:
            self.config_readed['status'] = HedgingStatus.OPENED_LONG
            self._add_trade_to_df(trade_type, amount, trading_pair, price)
            self.max_price = Decimal(0.00)
            self.min_price = Decimal(1_000_000)
        elif self.config_readed['status'] == HedgingStatus.OPENING_SHORT:
            self.config_readed['status'] = HedgingStatus.OPENED_SHORT
            self._add_trade_to_df(trade_type, amount, trading_pair, price)
            self.max_price = Decimal(0.00)
            self.min_price = Decimal(1_000_000)
        elif self.config_readed['status'] == HedgingStatus.CLOSING_LONG:
            self.config_readed['status'] = HedgingStatus.CLOSED_LONG
            self._close_trade_in_df(price)
            last_trade = self.df.iloc[-1]
            self.config_readed['BUY']['entry'] = last_trade['Exit price'] * (1 + Decimal(self.config_readed['BUY']['ts_activation']))
            if self.config_readed['BUY']['entry'] < self.config_readed['BUY']['entry_orig']:
                self.config_readed['BUY']['entry'] = self.config_readed['BUY']['entry_orig']
            # self.config_readed['BUY']['entry'] = max(last_trade['Exit price'] * (1 - Decimal(self.config_readed['BUY']['ts_activation'])), self.config_readed['BUY']['entry_orig'])
            # Viejo metodo documentado en Inkscape
            # self.config_readed['BUY']['sl'] = last_trade['Exit price']
            self.config_readed['BUY']['sl'] = round(self.config_readed['BUY']['entry'] * (1 - Decimal(self.config_readed['BUY']['sl_pct'])), 2)

            trailing_entry_activation = last_trade['Exit price'] * (1 - Decimal(self.config_readed['BUY']['ts_activation']))
            if trailing_entry_activation > self.config_readed['BUY']['entry_orig']:
                self.logger().info(f"LONG TRAILING ENTRY ACTIVATION Price: {price} - {trailing_entry_activation} > {self.config_readed['BUY']['entry_orig']}")
        elif self.config_readed['status'] == HedgingStatus.CLOSING_SHORT:
            self.config_readed['status'] = HedgingStatus.CLOSED_SHORT
            self._close_trade_in_df(price)
            last_trade = self.df.iloc[-1]            
            self.config_readed['SELL']['entry'] = last_trade['Exit price'] * (1 - Decimal(self.config_readed['SELL']['ts_activation']))
            if self.config_readed['SELL']['entry'] > self.config_readed['SELL']['entry_orig']:
                self.config_readed['SELL']['entry'] = self.config_readed['SELL']['entry_orig']
            # Viejo metodo documentado en Inkscape
            # self.config_readed['SELL']['sl'] = last_trade['Exit price']
            self.config_readed['SELL']['sl'] = round(self.config_readed['SELL']['entry'] * (1 + Decimal(self.config_readed['SELL']['sl_pct'])), 2)

            trailing_entry_activation = last_trade['Exit price'] * (1 + Decimal(self.config_readed['SELL']['ts_activation']))
            if trailing_entry_activation < self.config_readed['SELL']['entry_orig']:
                self.logger().info(f"SHORT TRAILING ENTRY ACTIVATION Price: {price} - {trailing_entry_activation} < {self.config_readed['SELL']['entry_orig']}")

        current_time = datetime.now()

        if self.config_readed['status'] == HedgingStatus.OPENED_LONG or self.config_readed['status'] == HedgingStatus.OPENED_SHORT:
            self.config_readed['entry_price'] = price
            self.config_readed['amount'] = amount
            self.config_readed['entry_time'] = current_time.strftime("%Y-%m-%d %H:%M:%S")
        elif self.config_readed['status'] == HedgingStatus.CLOSED_LONG or self.config_readed['status'] == HedgingStatus.CLOSED_SHORT:
            del self.config_readed['entry_price']
            del self.config_readed['amount']
            del self.config_readed['entry_time']
            self.max_price = Decimal(0.00)
            if current_time > self.config_readed['BUY']['not_after'] and current_time > self.config_readed['SELL']['not_after']:
                self.config_readed['status'] = HedgingStatus.STOPPED
            else:
                self.config_readed['status'] = HedgingStatus.WAITING

        self.write_file(True)

        Notificator().notify(f"Order filled. Dry run: {self.config_readed['dry_run']}", msg)



    def format_status(self) -> str:
        price_pair = round(self.market_data_provider.get_price_by_type('hyperliquid_perpetual', self.pair, PriceType.MidPrice), 2)

        current_time = datetime.now()
        formatted_time = current_time.strftime("%Y-%m-%d %H:%M:%S")

        df_str = ""
        if len(self.df) > 0:
            df_str = self.dataframe_to_formatted_string()

        pair_pnl = round(self.total_pnl, 2)
        pair_status = self.config_readed['status'].name

        pair_status_str = f"{self.pair} PnL: {pair_pnl} -  Status: {pair_status}"
        price_status = f"{self.pair}: {price_pair} "

        return f"{formatted_time}   {price_status}  Dry run: {self.config_readed['dry_run']}\n\n{df_str}\n\n{pair_status_str}"


    def dataframe_to_formatted_string(self):
        """
        Convert DataFrame to a formatted string with specific ordering and formatting rules.

        Parameters:
        -----------
        df : pandas.DataFrame
            Input DataFrame with specified columns

        Returns:
        --------
        str
            Formatted string representation of the DataFrame
        """
        # Create a copy of the dataframe to avoid modifying the original
        df_copy = self.df.copy()

        # Format time columns to remove fractional seconds
        for time_col in ['Entry time', 'Exit time']:
            df_copy[time_col] = pd.to_datetime(df_copy[time_col], errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S')

        # Format  columns to 2 decimal places
        df_copy['Amount'] = df_copy['Amount'].apply(lambda x: f'{x:.4f}' if pd.notnull(x) else '')
        df_copy['Entry price'] = df_copy['Entry price'].apply(lambda x: f'{x:.2f}' if pd.notnull(x) else '')
        df_copy['Exit price'] = df_copy['Exit price'].apply(lambda x: f'{x:.2f}' if pd.notnull(x) else '')
        df_copy['Fees'] = df_copy['Fees'].apply(lambda x: f'{x:.2f}' if pd.notnull(x) else '')
        df_copy['PnL'] = df_copy['PnL'].apply(lambda x: f'{x:.2f}' if pd.notnull(x) else '')
        df_copy['PnL%'] = df_copy['PnL%'].apply(lambda x: f'{x:.2f}' if pd.notnull(x) else '')

        # Sort the DataFrame
        # First, create a sorting key that prioritizes Exit Time,
        # falling back to Entry Time if Exit Time is empty
        df_copy['sort_time'] = df_copy['Exit time'].fillna(df_copy['Entry time'])

        # Sort the DataFrame by the sorting time and then by Ticker
        df_sorted = df_copy.sort_values(['sort_time', 'Ticker'])

        # Remove the temporary sorting column
        df_sorted = df_sorted.drop(columns=['sort_time'])

        # Convert DataFrame to string
        # First, convert all columns to strings and handle NaN values
        df_str = df_sorted.applymap(lambda x: str(x) if not pd.isna(x) else '')

        # Prepare column headers
        headers = df_str.columns.tolist()

        # Calculate maximum width for each column
        col_widths = [max(len(str(h)), df_str[h].str.len().max()) for h in headers]

        # Create format string
        format_str = ' '.join([f'{{:{w}}}' for w in col_widths])

        # Generate output string
        output_lines = [format_str.format(*headers)]  # Header row

        # Add data rows
        for _, row in df_str.iterrows():
            output_lines.append(format_str.format(*row.tolist()))

        return '\n'.join(output_lines)


    def _add_trade_to_df(self, trade_type, amount, trading_pair, price, datetime_str=None):
        current_time = datetime.now()
        if datetime_str is not None:
            current_time = datetime.strptime(datetime_str, "%Y-%m-%d %H:%M:%S")
        side = ""
        # Sometimes amount received is wrong. I use values from config file instead
        if trade_type == "BUY" or trade_type == TradeType.BUY:
            side = "LONG"
            amount = Decimal(self.config_readed['BUY']['order_amount'])
        elif trade_type == "SELL" or trade_type == TradeType.SELL:
            side = "SHORT"
            amount = Decimal(self.config_readed['SELL']['order_amount'])

        fees = round(price * amount * self.taker_fee, 2)

        new_row = pd.DataFrame({
            'Ticker': [trading_pair],
            'Amount': [amount],
            'Actual price': [None],
            'Entry price': [price],
            'Exit price': [None],
            'Entry time': [current_time],
            'Exit time': [None],
            'Side': [side],
            'Fees': [fees],
            'PnL': [0.00],
            'PnL%': [0.00]
        })
        self.df = pd.concat([self.df, new_row], ignore_index=True)
        self.last_df_index = self.df.index[-1]



    def _close_trade_in_df(self, price):
        index = self.last_df_index

        amount = Decimal(self.df.loc[index, 'Amount'])
        fees = Decimal(self.df.loc[index, 'Fees']) + round(price * amount * Decimal(self.taker_fee), 2)
        price_diff = price - Decimal(self.df.loc[index, 'Entry price'])
        if self.df.loc[index, 'Side'] == "SHORT":
            price_diff = -price_diff
            # amount = Decimal(self.config_readed['SELL']['order_amount'])

        # NOTE that pnl_pct shows the % variation of price, not real profit/loss
        pnl_pct = (price_diff / Decimal(self.df.loc[index, 'Entry price'])) * 100
        pnl = price_diff * amount - fees

        self.df.loc[index, 'Fees'] = fees
        self.df.loc[index, 'PnL'] = pnl
        self.df.loc[index, 'PnL%'] = pnl_pct
        self.df.loc[index, 'Exit price'] = price
        self.df.loc[index, 'Exit time'] = datetime.now()

        self.total_pnl = self.df[self.df['Ticker'] == self.pair]['PnL'].sum()

        self.last_df_index = None



    def update_trade_in_df(self):
        actual_price = self.market_data_provider.get_price_by_type('hyperliquid_perpetual', self.pair, PriceType.MidPrice)

        index = self.last_df_index
        if index is None:
            return

        price_diff = actual_price - Decimal(self.df.loc[index, 'Entry price'])
        amount = Decimal(self.df.loc[index, 'Amount'])
        if self.df.loc[index, 'Side'] == "SHORT":
            price_diff = -price_diff

        pnl = (price_diff * amount) - Decimal(self.df.loc[index, 'Fees'])
        pnl_pct = (price_diff / Decimal(self.df.loc[index, 'Entry price'])) * 100

        self.df.loc[index, 'Actual price'] = actual_price
        self.df.loc[index, 'PnL'] = pnl
        # NOTE that pnl_pct shows the % variation of price, not real profit/loss
        self.df.loc[index, 'PnL%'] = pnl_pct

        self.total_pnl = self.df[self.df['Ticker'] == self.pair]['PnL'].sum()

