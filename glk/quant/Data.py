import numpy as np
import pandas as pd
import pytz


class Data:
    tz_string = 'America/Buenos_Aires'
    tz = pytz.timezone(tz_string)

    cols = ['Open', 'Low', 'High', 'Close', 'Volume']
    data = pd.DataFrame()
    mask = None
    filename = None

    def load_file(self, file_path, timestamp_column='timestamp', timestamp_unit='ms', localize=True):
        self.filename = f"Data/{file_path}"
        self.data = pd.read_csv(self.filename)
        return self._load(timestamp_column=timestamp_column,timestamp_unit=timestamp_unit,localize=localize)

    def load_df(self, df, timestamp_column='timestamp', timestamp_unit='ms', localize=True):
        self.data = df
        return self._load(timestamp_column=timestamp_column,timestamp_unit=timestamp_unit,localize=localize)

    def _load(self, timestamp_column='timestamp', timestamp_unit='ms', localize=True):
        if timestamp_unit is None:
            if timestamp_column is None:
                self.data['Date'] = pd.to_datetime(self.data.index)
            else:
                self.data['Date'] = pd.to_datetime(self.data[timestamp_column])
        else:
            if timestamp_column is None:
                self.data['Date'] = pd.to_datetime(self.data.index, unit=timestamp_unit)
            else:
                self.data['Date'] = pd.to_datetime(self.data[timestamp_column], unit=timestamp_unit)
        if localize:
            self.data['Date'] = self.data['Date'].dt.tz_localize('UTC')
            self.data['Date'] = self.data['Date'].dt.tz_convert(self.tz_string)
        self.data.set_index('Date', inplace=True)
        # data = data.loc[start_date_str:end_date_str]
        self.data.rename(columns=lambda x: x.capitalize(), inplace=True)
        self.data = self.data.drop(columns=[col for col in self.data.columns if col not in self.cols])
        self.data = self.data.astype(np.float64)
        # Make a default mask of all True, mask that has no effect when applied
        self.mask = pd.Series(True, index=self.data.index)
        return self.data



    # def load_file(self, file_path, timestamp_column='timestamp',  localize=True):
    #     self.filename = f"Data/{file_path}"
    #     df = pd.read_csv(self.filename)
    #     return self.load_df(self, df,  timestamp_unit=timestamp_column, localize=localize)



    def get_time_frequency(self):
        if not isinstance(self.data.index, pd.DatetimeIndex):
            raise ValueError("Input must have a datetime index")

        if len(self.data.index) < 2:
            raise ValueError("Input must have at least two elements")

        time_diff = self.data.index[1] - self.data.index[0]

        # Convert timedelta to seconds
        seconds = time_diff.total_seconds()

        # Define time units and their corresponding pandas frequency strings
        time_units = [
            (60 * 60 * 24 * 365, 'Y'),  # Years
            (60 * 60 * 24 * 30, 'M'),  # Months (approximate)
            (60 * 60 * 24 * 7, 'W'),  # Weeks
            (60 * 60 * 24, 'D'),  # Days
            (60 * 60, 'H'),  # Hours
            (60, 'min'),  # Minutes
            (1, 'S')  # Seconds
        ]

        for unit_seconds, unit_symbol in time_units:
            if seconds >= unit_seconds:
                count = int(seconds / unit_seconds)
                return f"{count}{unit_symbol}"

        # If the time difference is less than a second
        return "1U"  # Microseconds


    def prepare_for_db(self, ticker, source):
        self.data['Agreggation'] = self.get_time_frequency()
        self.data['Ticker'] = ticker
        self.data['Source'] = source
        self.data['DateTime'] = self.data.index



    def trim_data(self, begin_date, end_date):
        # I made an in place modification of the dataframe in order to avoid break external reference to this dataframe
        mask = (self.data.index >= begin_date) & (self.data.index <= end_date)
        indices_to_drop = self.data.index[~mask]
        self.data.drop(indices_to_drop, inplace=True)


    def create_valid_data_mask(self, *time_series):
        """
        Create a boolean mask where all given time series have valid values.

        Args:
        *time_series: Variable number of pandas Series objects

        Returns:
        pd.Series: Boolean mask where True indicates all series have valid values
        """
        if len(time_series) == 0:
            # Use all columns from self.data_inst.data as default
            time_series = [self.data[col] for col in self.data.columns]

        # Ensure all inputs are pandas Series
        for ts in time_series:
            if not isinstance(ts, pd.Series):
                raise TypeError("All arguments must be pandas Series objects")

        # Align all series to the same index
        aligned_series = pd.concat(time_series, axis=1, join='outer')

        # Create mask where all values are valid (not null, None, or NaN)
        self.mask = ~aligned_series.isnull().any(axis=1)
        return self.mask

    def get_data_without_buffer(self):
        return self.data[self.mask]
