import os
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum, auto
from typing import Dict, List, Optional, Any

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


class HedgingAction(Enum):
    OPEN_LONG = auto()
    CLOSE_LONG = auto()
    OPEN_SHORT = auto()
    CLOSE_SHORT = auto()


class HedgingStatus(Enum):
    WAITING = auto()
    IN_TIME = auto()
    TRAILING = auto()
    OP_IN_PROGRESS = auto()
    FINISHED = auto()



class GLKHedgeExitConfig(StrategyV2ConfigBase):
    script_file_name: str = Field(default_factory=lambda: os.path.basename(__file__))
    candles_config: List[CandlesConfig] = []
    conf_script: str = Field(
        client_data=ClientFieldData(
            prompt=lambda mi: "Enter the script with the configuration: ",
            prompt_on_new=True))


class GLKHedgeExit(StrategyV2Base):
    pair = None
    conf_file = None
    config_readed = None
    last_conf_timestamp = None
    current_price = None


    def __init__(self, connectors: Dict[str, ConnectorBase], config: Optional[GLKHedgeExitConfig] = None):
        super().__init__(connectors, config)
        market = list(self.markets.values())[0]
        self.pair = list(market)[0]


        # hb_app = HummingbotApplication.main_application()
        # self.conf_file = f"conf/scripts/{hb_app.strategy_file_name}"

        self.config = config
        self.conf_file = f"conf/scripts/{config.conf_script}"

   

    def on_tick(self):
        self.read_file()
        # Update price once per cycle
        self.current_price = self.market_data_provider.get_price_by_type('hyperliquid_perpetual', self.pair, PriceType.MidPrice)
        self.process_pair()
    

    def process_pair(self):
        if self.config_readed['status'] == HedgingStatus.FINISHED or self.config_readed['status'] == HedgingStatus.OP_IN_PROGRESS:
            return

        if self.config_readed['status'] == HedgingStatus.TRAILING:
            exit_price = self.calculate_trailing_prices(self.current_price, self.config_readed['exit_price'], self.config_readed['ts_pct'])
            if exit_price != self.config_readed['exit_price']:
                self.logger().info(f"Trailing price updated from {self.config_readed['exit_price']} to {exit_price}")
                self.config_readed['exit_price'] = exit_price
                self.write_file(True)

            if self.current_price < self.config_readed['exit_price']:
                return self.operate_pair(HedgingAction.CLOSE_LONG)

        current_status = self.config_readed['status']
        self.config_readed['status'] = HedgingStatus.WAITING

        current_time = datetime.now()
        if current_time < self.config_readed['not_before']:
            if current_status != self.config_readed['status']:
                self.write_file(True)
            return
        self.config_readed['status'] = HedgingStatus.IN_TIME

        if self.current_price <= self.config_readed['activation_price']:
            if current_status != self.config_readed['status']:
                self.write_file(True)
            return
        self.config_readed['status'] = HedgingStatus.TRAILING

        if current_status != self.config_readed['status']:
            self.write_file(True)


    

    def operate_pair(self, action: HedgingAction):
        if action == HedgingAction.OPEN_SHORT or action == HedgingAction.CLOSE_LONG:
            self.config_readed['status'] = HedgingStatus.OP_IN_PROGRESS
            self.write_file(True)
            amount = self.config_readed['amount']

            if self.config_readed['dry_run']:
                self._did_fill_order("SELL", amount, self.pair, self.current_price)
            else:
                self.sell(
                    connector_name='hyperliquid_perpetual',
                    trading_pair=self.pair,
                    amount=amount,
                    order_type=OrderType.MARKET,
                    price=self.current_price
                )



    def did_fill_order(self, event: OrderFilledEvent):
        return self._did_fill_order(event.trade_type, event.amount, event.trading_pair, event.price)


    # def did_complete_buy_order(self, event: BuyOrderCompletedEvent):
    #     return self._did_fill_order("BUY", event.base_asset_amount, self.pair, event.quote_asset_amount)
    #
    # def did_complete_sell_order(self, event: SellOrderCompletedEvent):
    #     return self._did_fill_order("SELL", event.base_asset_amount, self.pair, event.quote_asset_amount)


    def _did_fill_order(self, trade_type, amount, trading_pair, price):
        self.config_readed['status'] = HedgingStatus.FINISHED
        self.write_file(True)

        msg = f"{trade_type} {amount} of {trading_pair} at {price} Dry run: {self.config_readed['dry_run']}"
        Notificator().notify(f"Order filled. Dry run: {self.config_readed['dry_run']}", msg)



    def calculate_trailing_prices(self, actual_price: float, actual_trigger_price, trailing_pct) -> Decimal:
        """
        Calculates the trailing prices based on the actual price, actual trigger price, and trailing percentage.
        Only for long positions.

        Parameters:
        - actual_price (float): The current price of the asset.
        - actual_trigger_price (Decimal): The price at which the trailing stop loss is triggered.
        - trailing_pct (Decimal): The percentage below the actual price that the stop loss trails.

        Returns:
        - Decimal: The new trigger price based on the trailing stop loss calculation.
        """
        new_trigger_price = Decimal(str(actual_price)) * (Decimal('1') - trailing_pct)

        if new_trigger_price > actual_trigger_price:
            return new_trigger_price

        return actual_trigger_price
        



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
                
                # Convert numeric values to Decimal
                self._convert_to_decimal(self.config_readed)
                
                self.logger().info(f"Configuration file (re)readed")
                self.last_conf_timestamp = datetime.now().timestamp()

                self.config_readed['status'] = HedgingStatus[self.config_readed['status']]

                if previous_leverage is None or previous_leverage != self.config_readed['leverage']:
                    self.set_leverage('', self.pair, self.config_readed['leverage'])

                return True

            except yaml.YAMLError as e:
                print(f"Error reading YAML file: {e}")
                return False

    def _convert_to_decimal(self, data: Any):
        """
        Recursively convert numeric values (int, float) to Decimal in a dictionary.
        """
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, (int, float)) and key != 'leverage' and key != 'dry_run':
                    try:
                        data[key] = Decimal(str(value))
                    except InvalidOperation:
                        self.logger().warning(f"Could not convert value '{value}' for key '{key}' to Decimal. Keeping original value.")
                elif isinstance(value, (dict, list)):
                    self._convert_to_decimal(value)
        elif isinstance(data, list):
            for i, item in enumerate(data):
                if isinstance(item, (int, float)):
                    try:
                        data[i] = Decimal(str(item))
                    except InvalidOperation:
                        self.logger().warning(f"Could not convert list value '{item}' at index {i} to Decimal. Keeping original value.")
                elif isinstance(item, (dict, list)):
                    self._convert_to_decimal(item)
                    



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

 

    def format_status(self) -> str:
        current_time = datetime.now()
        formatted_time = current_time.strftime("%Y-%m-%d %H:%M:%S")
        pair_status = self.config_readed['status'].name

        format_status = f"{formatted_time}    Dry run: {self.config_readed['dry_run']}\n\n"
        format_status += f"{self.pair}\n\n"
        format_status += f"Activation time: {self.config_readed['not_before']}\n\n"
        format_status += f"Activation price: {round(self.config_readed['activation_price'], 2)}  Exit price: {round(self.config_readed['exit_price'], 2)}\n\n"
        format_status += f"Price: {round(self.current_price, 2)} \n\n"
        format_status += f"Status: {pair_status}"

        return format_status




    def set_leverage(self, connector: str, trading_pair: str, leverage: int):
        connector_name = connector
        if connector_name == '':
            connector_name = list(self.config.markets.keys())[0]
        perp_connector = self.connectors[connector_name]
        perp_connector.set_position_mode(PositionMode.ONEWAY)

        perp_connector.set_leverage(trading_pair=trading_pair, leverage=leverage)
        self.logger().info(f"Setting leverage to {leverage}x for {connector_name} on {trading_pair}")




    def create_actions_proposal(self) -> List[CreateExecutorAction]:
        create_actions = []
        return create_actions



    def stop_actions_proposal(self) -> List[StopExecutorAction]:
        stop_actions = []
        return stop_actions