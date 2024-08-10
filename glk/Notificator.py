from datetime import datetime
import pytz


class Notificator:
    local_tz = pytz.timezone('America/Buenos_Aires')

    @staticmethod
    def notify(summary, body):
        delimiter = "=" * 40
        current_time_local = datetime.now(Notificator.local_tz)
        formatted_time = current_time_local.strftime('%Y-%m-%d %H:%M:%S')
        with open('/tmp/hummingbot.txt', 'a') as file:
            file.write(f"{delimiter}\n{formatted_time}\nSummary: {summary}\nBody: {body}\n\n")
