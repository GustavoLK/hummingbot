import os
from datetime import datetime
from decimal import Decimal
from enum import Enum, auto
from typing import Dict, List, Optional

import yaml
from pydantic import Field

from glk.debug import enable_debugging
from glk.model.database import get_postgresql_session
from glk.model.Transaction import TransactionStatus
from glk.model.TransactionGroup import TransactionGroup
from glk.Notificator import Notificator
from hummingbot.client.config.config_data_types import ClientFieldData
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.data_type.common import OrderType, PositionMode, PriceType
from hummingbot.core.event.events import OrderFilledEvent
from hummingbot.data_feed.candles_feed.data_types import CandlesConfig
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, StopExecutorAction

enable_debugging()


class StrategyAction(Enum):
    BUY = auto()
    SELL = auto()


class StrategyStatus(Enum):
    RUNNING = auto()
    EXECUTING = auto()
    FINISHED = auto()
    ERROR = auto()


class MetatraderCopierConfig(StrategyV2ConfigBase):
    script_file_name: str = Field(default_factory=lambda: os.path.basename(__file__))
    candles_config: List[CandlesConfig] = []
    conf_script: str = Field(
        client_data=ClientFieldData(
            prompt=lambda mi: "Enter the script with the configuration: ",
            prompt_on_new=True))


class MetatraderCopier(StrategyV2Base):
    pair = None
    conf_file = None
    config_readed = None
    last_conf_timestamp = None
    current_price = None

    def __init__(self, connectors: Dict[str, ConnectorBase], config: Optional[MetatraderCopierConfig] = None):
        super().__init__(connectors, config)
        market = list(self.markets.values())[0]
        self.pair = list(market)[0]


        # hb_app = HummingbotApplication.main_application()
        # self.conf_file = f"conf/scripts/{hb_app.strategy_file_name}"

        self.config = config
        self.conf_file = f"conf/scripts/{config.conf_script}"
        self.session = get_postgresql_session()
        self.current_trx = None
        # para el calculo de precio promedio de ordenes market que se ejecutan por partes
        self.orden_volume = Decimal(0)
        self.acum_precio_volume = Decimal(0)
        self.acum_volume = Decimal(0)



    def on_tick(self):
        self.read_file()
        # Update price once per cycle
        self.current_price = self.market_data_provider.get_price_by_type('hyperliquid_perpetual', self.pair, PriceType.MidPrice)
        self.process_pair()




    def process_pair(self):
        if self.config_readed['status'] in [StrategyStatus.FINISHED, StrategyStatus.EXECUTING, StrategyStatus.ERROR]:
            return

        current_time = datetime.now()
        current_status = self.config_readed['status']

        if current_status == StrategyStatus.RUNNING:
            if current_time > self.config_readed['close_time']:
                self.config_readed['status'] = StrategyStatus.FINISHED
                self.write_file(True)
                return

        transaction_group = self.session.query(TransactionGroup).filter(
            TransactionGroup.id == self.config_readed['transaction_group_id']
        ).first()

        if transaction_group is None:
            self.logger().error(f"Transaction group not found: {self.config_readed['transaction_group_id']}")
            self.config_readed['status'] = StrategyStatus.ERROR
            self.write_file(True)
            return

        # Me traigo las transacciones de los ultimos 10 segundos
        last_transactions = transaction_group.get_pending_transactions(10)
        if len(last_transactions) == 0:
            return
        self.current_trx = last_transactions[-1]
        self.session.add(self.current_trx)
        self.current_trx.status = TransactionStatus.EXECUTING.value
        self.session.commit()
        action = StrategyAction.BUY if self.current_trx.quantity > 0 else StrategyAction.SELL
        self.operate_pair(action, abs(self.current_trx.quantity))




    def operate_pair(self, action: StrategyAction, amount: float):
        self.orden_volume = amount
        self.acum_volume = Decimal(0)
        self.acum_precio_volume = Decimal(0)
        self.config_readed['status'] = StrategyStatus.EXECUTING

        if self.config_readed['dry_run']:
            self._did_fill_order(action, amount, self.pair, self.current_price)
        else:
            if action == StrategyAction.SELL:
                self.sell(
                    connector_name='hyperliquid_perpetual',
                    trading_pair=self.pair,
                    amount=amount,
                    order_type=OrderType.MARKET,
                    price=self.current_price
                )
            elif action == StrategyAction.BUY:
                self.buy(
                    connector_name='hyperliquid_perpetual',
                    trading_pair=self.pair,
                    amount=amount,
                    order_type=OrderType.MARKET,
                    price=self.current_price
                )




    def did_fill_order(self, event: OrderFilledEvent):
        return self._did_fill_order(event.order_id, event.trade_type, event.amount, event.trading_pair, event.price)





    def _did_fill_order(self, order_id, trade_type, amount, trading_pair, price):
        self.acum_precio_volume += Decimal(str(price)) * Decimal(str(amount))
        self.acum_volume += Decimal(str(amount))
        if self.acum_volume != self.orden_volume:
            return
        avg_price = self.acum_precio_volume / self.acum_volume

        if self.config_readed['status'] == StrategyStatus.EXECUTING:
            self.config_readed['status'] = StrategyStatus.RUNNING
        try:
            self.write_file(True)
            self.session.add(self.current_trx)
            self.current_trx.datetime = datetime.now()
            self.current_trx.status = TransactionStatus.EXECUTED.value
            self.current_trx.price = avg_price
            self.current_trx.commission = Decimal(str(price)) * Decimal(str(amount)) * Decimal('0.000432')  # 0.0432% de comision en hyperliquid
            self.current_trx.leverage = self.config_readed['leverage']
            self.session.commit()
            self.current_trx = None

            trx_grp = self.session.query(TransactionGroup).filter(
                TransactionGroup.id == self.config_readed['transaction_group_id']
            ).first()
            self.session.add(trx_grp)
            trx_grp.calc_from_transactions()
            self.session.commit()
        except Exception as e:
            self.logger().error(f"Error updating transaction: {e}")
            self.session.rollback()

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

                self.config_readed['status'] = StrategyStatus[self.config_readed['status']]

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
        # pair_status = self.config_readed['status'].name  # Unused variable

        format_status = f"{formatted_time}    Dry run: {self.config_readed['dry_run']}\n\n"
        format_status += f"{self.pair}\n\n"

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
