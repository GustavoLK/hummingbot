from sqlalchemy import Column, Float, Integer, PrimaryKeyConstraint, String, Table, inspect
from sqlalchemy.dialects.postgresql import TIMESTAMP, insert
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker


class CustomBase:
    @staticmethod
    def _get_sql_type(dtype):
        if "int" in str(dtype):
            return Integer
        elif "float" in str(dtype):
            return Float
        elif "datetime" in str(dtype):
            return TIMESTAMP(timezone=True)
        else:
            return String


Base = declarative_base(cls=CustomBase)


class OHLCV(Base):
    __tablename__ = 'ohlcv'

    source = Column(String, primary_key=True)
    ticker = Column(String, primary_key=True)
    agreggation = Column(String, primary_key=True)
    datetime = Column(TIMESTAMP(timezone=True), primary_key=True)

    # Other columns will be added dynamically

    __table_args__ = (PrimaryKeyConstraint('source', 'ticker', 'agreggation', 'datetime'),)

    @classmethod
    def create_table(cls, engine, df):
        df.columns = df.columns.str.lower()
        # Dynamically add columns based on DataFrame
        for col, dtype in df.dtypes.items():
            col = col.lower()  # Ensure column names are lowercase
            if col not in ['source', 'ticker', 'agreggation', 'datetime']:
                if not hasattr(cls, col):
                    setattr(cls, col, Column(name=col, type_=cls._get_sql_type(dtype)))

        if not inspect(engine).has_table(cls.__tablename__):
            Base.metadata.create_all(engine)

    @classmethod
    def save_data(cls, engine, df):
        df.columns = df.columns.str.lower()
        Session = sessionmaker(bind=engine)
        session = Session()

        # Convert DataFrame to list of dictionaries
        data = df.to_dict('records')

        # Get all column names
        columns = [column.key for column in cls.__table__.columns]

        # Create the insert statement
        stmt = insert(cls.__table__)

        # Add ON CONFLICT clause
        update_dict = {c: getattr(stmt.excluded, c) for c in columns if
                       c not in ['source', 'ticker', 'agreggation', 'datetime']}
        stmt = stmt.on_conflict_do_update(
            index_elements=['source', 'ticker', 'agreggation', 'datetime'],
            set_=update_dict
        )

        # Insert data in batches
        batch_size = 1000
        for i in range(0, len(data), batch_size):
            batch = data[i:i + batch_size]
            session.execute(stmt, batch)
            session.commit()

        session.close()

        print(f"Data successfully upserted into table '{cls.__tablename__}' in batches")
