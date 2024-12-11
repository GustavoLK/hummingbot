import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum, auto
import random
from typing import Dict, Optional, List

import pandas as pd
import yaml
from hummingbot.connector.connector_base import ConnectorBase
from pydantic import Field

from glk.Notificator import Notificator
from hummingbot.client.hummingbot_application import HummingbotApplication
from hummingbot.core.data_type.common import OrderType, PositionMode, PriceType
from hummingbot.core.event.events import OrderFilledEvent
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, StopExecutorAction




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


class GLKHedgeOptionsFileConfig(StrategyV2ConfigBase):
    script_file_name: str = Field(default_factory=lambda: os.path.basename(__file__))
    candles_config: List[CandlesConfig] = []


class GLKHedgeOptionsFile(StrategyV2Base):
    conf_file = "/etc/hummingbot/OptionsHedge.yml"
    config_readed = None
    last_conf_timestamp = None
    positions = {
        "Pair-01": {
            "entry_price": None,
            "status": HedgingStatus.WAITING
        },
        "Pair-02": {
            "entry_price": None,
            "status": HedgingStatus.WAITING
        }
    }


    def __init__(self, connectors: Dict[str, ConnectorBase], config: Optional[GLKHedgeOptionsFileConfig] = None):
        super().__init__(connectors, config)

        # hb_app = HummingbotApplication.main_application()
        # self.conf_file = f"conf/scripts/{hb_app.strategy_file_name}"

        self.config = config
        self.df = pd.DataFrame(columns=[
            'Ticker',
            'Actual price',
            'Entry price',
            'Exit price',
            'Entry time',
            'Exit time',
            'Side',
            'PnL',
            'PnL%'
        ])

        # Optional: Set data types for the columns
        self.df = self.df.astype({
            'Ticker': 'object',
            'Actual price': 'float64',
            'Entry price': 'float64',
            'Exit price': 'float64',
            'Entry time': 'datetime64[ns]',
            'Exit time': 'datetime64[ns]',
            'Side': 'object',
            'PnL': 'float64',
            'PnL%': 'float64'
        })



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
                self.config_readed = yaml.safe_load(file)
                self.last_conf_timestamp = datetime.now().timestamp()

                self.set_leverage('', 'Pair-01', self.config_readed['Pair-01']['leverage'])
                self.set_leverage('', 'Pair-02', self.config_readed['Pair-02']['leverage'])
                return True

            except yaml.YAMLError as e:
                print(f"Error reading YAML file: {e}")
                return False

    def set_leverage(self, connector: str, trading_pair: str, leverage: int):
        connector_name = connector
        if connector_name == '':
            connector_name = list(self.config.markets.keys())[0]
        perp_connector = self.connectors[connector_name]
        perp_connector.set_position_mode(PositionMode.ONEWAY)
        pair_data = self.get_pair_config(trading_pair)
        perp_connector.set_leverage(trading_pair=pair_data['pair'], leverage=leverage)
        self.logger().info(f"Setting leverage to {leverage}x for {connector_name} on {pair_data['pair']}")

    def on_tick(self):
        self.read_file()
        self.process_pair('Pair-01')
        self.process_pair('Pair-02')

        current_time = datetime.now()
        if current_time.second % 10 == 0:
            self.generate_random_trades()

        if self.positions['Pair-01']['status'] == HedgingStatus.STOPPED and self.positions['Pair-02']['status'] == HedgingStatus.STOPPED:
            self.logger().info(f"Stopping application")
            HummingbotApplication.main_application().stop()

    def create_actions_proposal(self) -> List[CreateExecutorAction]:
        create_actions = []
        return create_actions

    def get_pair_config(self, pair):
        pair_config = self.config_readed[pair]
        if "reverse_exit" not in pair_config or pair_config['reverse_exit'] is None:
            pair_config['reverse_exit'] = False
        pair_config_buy = pair_config['BUY']
        pair_config_sell = pair_config['SELL']
        pair_position = self.positions[pair]
        output_pair = pair_config['ticker']


        return {
            'pair_config': pair_config,
            'pair_config_buy': pair_config_buy,
            'pair_config_sell': pair_config_sell,
            'pair_position': pair_position,
            'pair': output_pair
        }

    def get_reverse_pair(self, pair):
        pair1 = self.get_pair_config('Pair-01')
        pair2 = self.get_pair_config('Pair-02')

        if pair == pair1['pair']:
            return 'Pair-01'
        elif pair == pair2['pair']:
            return 'Pair-02'



    def process_pair(self, pair):
        pair_data = self.get_pair_config(pair)

        price = self.market_data_provider.get_price_by_type('hyperliquid_perpetual', pair_data['pair'],
                                                            PriceType.MidPrice)

        current_time = datetime.now()
        if current_time > pair_data['pair_config']['not_after']:
            if pair_data['pair_position']['status'] == HedgingStatus.WAITING:
                self.positions[pair]['status'] = HedgingStatus.STOPPED
                return
            elif pair_data['pair_position']['status'] == HedgingStatus.OPENED_LONG:
                self.operate_pair(pair, HedgingAction.CLOSE_LONG)
                return
            elif pair_data['pair_position']['status'] == HedgingStatus.OPENED_SHORT:
                self.operate_pair(pair, HedgingAction.CLOSE_SHORT)
                return


        if pair_data['pair_position']['status'] == HedgingStatus.WAITING:
            if price > pair_data['pair_config_buy']['trigger_price']:
                self.operate_pair(pair, HedgingAction.OPEN_LONG)
                return
            if price < pair_data['pair_config_sell']['trigger_price']:
                self.operate_pair(pair, HedgingAction.OPEN_SHORT)
                return

        # self.reverse_exit es usado con fines de testing. En produccion reverse_exit es False y la salida es por stop loss
        # pero en test se setea a True para que la salida sea con ganancia de forma de no perder guita en cada prueba
        if not pair_data['pair_config']['reverse_exit']:
            if pair_data['pair_position']['status'] == HedgingStatus.OPENED_LONG:
                if price < (pair_data['pair_position']['entry_price'] * Decimal(1 - pair_data['pair_config_buy']['stop_loss'])):
                    self.operate_pair(pair, HedgingAction.CLOSE_LONG)
                    return
            if pair_data['pair_position']['status'] == HedgingStatus.OPENED_SHORT:
                if price > (pair_data['pair_position']['entry_price'] * Decimal(1 + pair_data['pair_config_sell']['stop_loss'])):
                    self.operate_pair(pair, HedgingAction.CLOSE_SHORT)
                    return
        else:
            if pair_data['pair_position']['status'] == HedgingStatus.OPENED_LONG:
                if price > (pair_data['pair_position']['entry_price'] * Decimal(1 + pair_data['pair_config_buy']['stop_loss'])):
                    self.operate_pair(pair, HedgingAction.CLOSE_LONG)
                    return
            if pair_data['pair_position']['status'] == HedgingStatus.OPENED_SHORT:
                if price < (pair_data['pair_position']['entry_price'] * Decimal(1 - pair_data['pair_config_sell']['stop_loss'])):
                    self.operate_pair(pair, HedgingAction.CLOSE_SHORT)
                    return



    def operate_pair(self, pair, action: HedgingAction):
        pair_data = self.get_pair_config(pair)
        price = Decimal(self.market_data_provider.get_price_by_type('hyperliquid_perpetual', pair_data['pair'],
                                                                    PriceType.MidPrice))

        if action == HedgingAction.OPEN_LONG or action == HedgingAction.CLOSE_SHORT:
            self.buy(
                connector_name='hyperliquid_perpetual',
                trading_pair=pair_data['pair'],
                amount=Decimal(pair_data['pair_config_buy']['order_amount']),
                order_type=OrderType.MARKET,
                price=price
            )
        elif action == HedgingAction.OPEN_SHORT or action == HedgingAction.CLOSE_LONG:
            self.sell(
                connector_name='hyperliquid_perpetual',
                trading_pair=pair_data['pair'],
                amount=Decimal(pair_data['pair_config_sell']['order_amount']),
                order_type=OrderType.MARKET,
                price=price
            )

        if action == HedgingAction.OPEN_LONG:
            self.positions[pair]['status'] = HedgingStatus.OPENING_LONG
        elif action == HedgingAction.OPEN_SHORT:
            self.positions[pair]['status'] = HedgingStatus.OPENING_SHORT
        elif action == HedgingAction.CLOSE_LONG:
            self.positions[pair]['status'] = HedgingStatus.CLOSING_LONG
        elif action == HedgingAction.CLOSE_SHORT:
            self.positions[pair]['status'] = HedgingStatus.CLOSING_SHORT


    def stop_actions_proposal(self) -> List[StopExecutorAction]:
        stop_actions = []
        return stop_actions


    def did_fill_order(self, event: OrderFilledEvent):
        msg = f"{event.trade_type.name} {event.amount} of {event.trading_pair} at {event.price}"
        pair = self.get_reverse_pair(event.trading_pair)

        if self.positions[pair]['status'] == HedgingStatus.OPENING_LONG:
            self.positions[pair]['status'] = HedgingStatus.OPENED_LONG
            self.positions[pair]['entry_price'] = event.price
        elif self.positions[pair]['status'] == HedgingStatus.OPENING_SHORT:
            self.positions[pair]['status'] = HedgingStatus.OPENED_SHORT
            self.positions[pair]['entry_price'] = event.price
        elif self.positions[pair]['status'] == HedgingStatus.CLOSING_LONG:
            self.positions[pair]['status'] = HedgingStatus.CLOSED_LONG
            self.positions[pair]['entry_price'] = None
        elif self.positions[pair]['status'] == HedgingStatus.CLOSING_SHORT:
            self.positions[pair]['status'] = HedgingStatus.CLOSED_SHORT
            self.positions[pair]['entry_price'] = None

        pair_data = self.get_pair_config(pair)
        if self.positions[pair]['status'] == HedgingStatus.CLOSED_LONG or self.positions[pair]['status'] == HedgingStatus.CLOSED_SHORT:
            current_time = datetime.now()
            if current_time < pair_data['pair_config']['not_after']:
                self.positions[pair]['status'] = HedgingStatus.WAITING
            else:
                self.positions[pair]['status'] = HedgingStatus.STOPPED
            # Si estoy en modo test ( no salgo con stop loss sino con ganancia ) luego de ejecutada la operacion la pongo
            # en stop, de lo contrario volveria a ingresar la mismas operacion y con la misma direccion que la que acaba de cerrar
            if pair_data['pair_config']['reverse_exit']:
                self.positions[pair]['status'] = HedgingStatus.STOPPED

        Notificator().notify("Order filled", msg)



    def format_status(self) -> str:
        pair_data_btc = self.get_pair_config('Pair-01')
        pair_data_eth = self.get_pair_config('Pair-02')

        price_btc = self.market_data_provider.get_price_by_type('hyperliquid_perpetual', pair_data_btc['pair'], PriceType.MidPrice)
        price_eth = self.market_data_provider.get_price_by_type('hyperliquid_perpetual', pair_data_eth['pair'], PriceType.MidPrice)

        current_time = datetime.now()
        formatted_time = current_time.strftime("%Y-%m-%d %H:%M:%S")

        df_str = ""
        if len(self.df) > 0:
            df_str = self.dataframe_to_formatted_string()

        return f"{formatted_time}   {pair_data_btc['pair']}: {price_btc}    {pair_data_eth['pair']}: {price_eth}\n\n{df_str}"


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

        # Format PnL columns to 2 decimal places
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



    def generate_random_trades(self, num_trades=5):
        """
        Generate a DataFrame with random trading data.

        Parameters:
        -----------
        num_trades : int, optional (default=5)
            Number of random trades to generate

        Returns:
        --------
        pandas.DataFrame
            DataFrame filled with random trading data
        """
        # Prepare lists to store data
        tickers = ['AVAX-USD', 'ETH-USD']
        sides = ['LONG', 'SHORT']

        # Initialize DataFrame columns
        columns = [
            'Ticker', 'Actual price', 'Entry price', 'Exit price',
            'Entry time', 'Exit time', 'Side', 'PnL', 'PnL%'
        ]

        # Create an empty DataFrame with the right columns
        df2 = pd.DataFrame(columns=columns)

        for _ in range(num_trades):
            # Randomly decide if the trade is closed or still open
            is_closed = random.choice([True, False])

            # Generate entry time
            entry_time = datetime.now() - timedelta(days=random.randint(1, 30))

            # Generate trade details
            ticker = random.choice(tickers)
            side = random.choice(sides)
            entry_price = round(random.uniform(50, 500), 2)
            actual_price = round(entry_price * random.uniform(0.9, 1.1), 2)

            # Determine exit details
            if is_closed:
                exit_time = entry_time + timedelta(days=random.randint(1, 10))
                exit_price = round(entry_price * random.uniform(0.8, 1.2), 2)

                # Calculate PnL
                if side == 'LONG':
                    pnl = exit_price - entry_price
                else:  # SHORT
                    pnl = entry_price - exit_price

                pnl_percentage = round((pnl / entry_price) * 100, 2)
            else:
                # If trade is not closed, leave exit details empty
                exit_time = None
                exit_price = None
                pnl = None
                pnl_percentage = None

            # Create a new row in the DataFrame
            new_row = pd.DataFrame({
                'Ticker': [ticker],
                'Actual price': [actual_price],
                'Entry price': [entry_price],
                'Exit price': [exit_price],
                'Entry time': [entry_time],
                'Exit time': [exit_time],
                'Side': [side],
                'PnL': [pnl],
                'PnL%': [pnl_percentage]
            })

            # Concatenate the new row to the DataFrame
            df2 = pd.concat([df2, new_row], ignore_index=True)

        # Ensure correct data types
        df2 = df2.astype({
            'Ticker': 'object',
            'Actual price': 'float64',
            'Entry price': 'float64',
            'Exit price': 'float64',
            'Entry time': 'datetime64[ns]',
            'Exit time': 'datetime64[ns]',
            'Side': 'object',
            'PnL': 'float64',
            'PnL%': 'float64'
        })

        # self.df = pd.concat([self.df, df2], ignore_index=True)
        self.df = df2
