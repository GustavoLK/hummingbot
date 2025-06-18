import os
from datetime import datetime
from decimal import Decimal
from enum import Enum, auto
from typing import Dict, List, Optional

import yaml
from pydantic import Field

from glk.debug import enable_debugging
from glk.Notificator import Notificator
from hummingbot.client.config.config_data_types import ClientFieldData
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import OrderType, PositionMode, PriceType
from hummingbot.core.event.events import OrderFilledEvent
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, StopExecutorAction

enable_debugging()


class HedgingAction(Enum):
    OPEN_LONG = auto()
    CLOSE_LONG = auto()
    OPEN_SHORT = auto()
    CLOSE_SHORT = auto()


class HedgingStatus(Enum):
    WAITING = auto()
    OPENING_LONG = auto()
    OPENED_LONG = auto()
    OPENING_SHORT = auto()
    OPENED_SHORT = auto()
    CLOSING_LONG = auto()
    CLOSING_SHORT = auto()
    FINISHED = auto()


class GLKHedgeStrangleConfig(StrategyV2ConfigBase):
    script_file_name: str = Field(default_factory=lambda: os.path.basename(__file__))
    candles_config: List[CandlesConfig] = []
    conf_script: str = Field(
        client_data=ClientFieldData(
            prompt=lambda mi: "Enter the script with the configuration: ",
            prompt_on_new=True))


class GLKHedgeStrangle(StrategyV2Base):
    pair = None
    conf_file = None
    config_readed = None
    last_conf_timestamp = None
    current_price = None

    def __init__(self, connectors: Dict[str, ConnectorBase], config: Optional[GLKHedgeStrangleConfig] = None):
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
        if self.config_readed['status'] in [HedgingStatus.FINISHED, HedgingStatus.OPENING_LONG, HedgingStatus.OPENING_SHORT, HedgingStatus.CLOSING_LONG, HedgingStatus.CLOSING_SHORT]:
            return

        current_time = datetime.now()
        current_status = self.config_readed['status']

        if current_status == HedgingStatus.WAITING:
            if current_time > self.config_readed['close_time']:
                self.config_readed['status'] = HedgingStatus.FINISHED
                self.write_file(True)
                return
            if self.current_price > self.config_readed['LONG']['entry_price']:
                self.operate_pair(HedgingAction.OPEN_LONG)
            elif self.current_price < self.config_readed['SHORT']['entry_price']:
                self.operate_pair(HedgingAction.OPEN_SHORT)
            return

        elif current_status == HedgingStatus.OPENED_LONG:
            # Si llegue al valor del stop loss, cierro la posicion sin importar la hora de cierre
            if self.current_price < self.config_readed['LONG']['exit_price']:
                self.operate_pair(HedgingAction.CLOSE_LONG)
                return
            # Las demas acciones se solo se realizan luego de la hora de cierre
            if current_time > self.config_readed['close_time']:
                # Si estoy en ganancia, cierro la posicion
                if self.current_price > self.config_readed['LONG']['entry_price']:
                    self.operate_pair(HedgingAction.CLOSE_LONG)
                    return
                # Si estoy en perdida, cierro la posicion solo si esta configurado para hacerlo
                elif self.config_readed['LONG']['exit_with_loss']:
                    self.operate_pair(HedgingAction.CLOSE_LONG)
                    return

        elif current_status == HedgingStatus.OPENED_SHORT:
            # Si llegue al valor del stop loss, cierro la posicion sin importar la hora de cierre
            if self.current_price > self.config_readed['SHORT']['exit_price']:
                self.operate_pair(HedgingAction.CLOSE_SHORT)
                return
            # Las demas acciones se solo se realizan luego de la hora de cierre
            if current_time > self.config_readed['close_time']:
                # Si estoy en ganancia, cierro la posicion
                if self.current_price < self.config_readed['SHORT']['entry_price']:
                    self.operate_pair(HedgingAction.CLOSE_SHORT)
                    return
                # Si estoy en perdida, cierro la posicion solo si esta configurado para hacerlo
                elif self.config_readed['SHORT']['exit_with_loss']:
                    self.operate_pair(HedgingAction.CLOSE_SHORT)
                    return




    def operate_pair(self, action: HedgingAction):
        if action == HedgingAction.OPEN_LONG:
            self.config_readed['status'] = HedgingStatus.OPENING_LONG
            amount = self.config_readed['LONG']['amount']
        elif action == HedgingAction.OPEN_SHORT:
            self.config_readed['status'] = HedgingStatus.OPENING_SHORT
            amount = self.config_readed['SHORT']['amount']
        elif action == HedgingAction.CLOSE_LONG:
            self.config_readed['status'] = HedgingStatus.CLOSING_LONG
            amount = self.config_readed['LONG']['amount']
        elif action == HedgingAction.CLOSE_SHORT:
            self.config_readed['status'] = HedgingStatus.CLOSING_SHORT
            amount = self.config_readed['SHORT']['amount']

        if self.config_readed['dry_run']:
            self._did_fill_order(action, amount, self.pair, self.current_price)
        else:
            if action == HedgingAction.OPEN_SHORT or action == HedgingAction.CLOSE_LONG:
                self.sell(
                    connector_name='hyperliquid_perpetual',
                    trading_pair=self.pair,
                    amount=amount,
                    order_type=OrderType.MARKET,
                    price=self.current_price
                )
            elif action == HedgingAction.OPEN_LONG or action == HedgingAction.CLOSE_SHORT:
                self.buy(
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
        if self.config_readed['status'] == HedgingStatus.OPENING_LONG:
            self.config_readed['status'] = HedgingStatus.OPENED_LONG
        elif self.config_readed['status'] == HedgingStatus.OPENING_SHORT:
            self.config_readed['status'] = HedgingStatus.OPENED_SHORT
        elif self.config_readed['status'] == HedgingStatus.CLOSING_LONG:
            self.config_readed['status'] = HedgingStatus.WAITING
        elif self.config_readed['status'] == HedgingStatus.CLOSING_SHORT:
            self.config_readed['status'] = HedgingStatus.WAITING
        self.write_file(True)

        msg = f"{trade_type} {amount} of {trading_pair} at {price} Dry run: {self.config_readed['dry_run']}"
        Notificator().notify("Order filled. Dry run: " + str(self.config_readed['dry_run']), msg)





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

                self.logger().info("Configuration file (re)readed")
                self.last_conf_timestamp = datetime.now().timestamp()

                self.config_readed['status'] = HedgingStatus[self.config_readed['status']]
                self.config_readed['LONG']['entry_price'] = Decimal(str(self.config_readed['LONG']['entry_price']))
                self.config_readed['LONG']['exit_price'] = Decimal(str(self.config_readed['LONG']['exit_price']))
                self.config_readed['LONG']['amount'] = Decimal(str(self.config_readed['LONG']['amount']))
                self.config_readed['SHORT']['entry_price'] = Decimal(str(self.config_readed['SHORT']['entry_price']))
                self.config_readed['SHORT']['exit_price'] = Decimal(str(self.config_readed['SHORT']['exit_price']))
                self.config_readed['SHORT']['amount'] = Decimal(str(self.config_readed['SHORT']['amount']))

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




    def format_status(self) -> str:
        current_time = datetime.now()
        formatted_time = current_time.strftime("%Y-%m-%d %H:%M:%S")
        pair_status = self.config_readed['status'].name

        format_status = f"{formatted_time}    Dry run: {self.config_readed['dry_run']}\n\n"
        format_status += f"{self.pair}\n\n"
        format_status += f"Close time: {self.config_readed['close_time']}\n\n"
        format_status += f"LONG  Activation price: {round(self.config_readed['LONG']['entry_price'], 2)}  Exit price: {round(self.config_readed['LONG']['exit_price'], 2)}\n\n"
        format_status += f"SHORT Activation price: {round(self.config_readed['SHORT']['entry_price'], 2)}  Exit price: {round(self.config_readed['SHORT']['exit_price'], 2)}\n\n"
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
