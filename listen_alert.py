import re
import time
from datetime import datetime

import dbus
import pygame
import pytz
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

# Initialize pygame mixer
pygame.mixer.init()


# Function to play sound
def play_sound(sound_file):
    sound = pygame.mixer.Sound(sound_file)
    sound.play()


# Custom event handler class
class NotificationHandler(FileSystemEventHandler):
    signal_file = "/tmp/hummingbot.txt"
    local_tz = pytz.timezone('America/Buenos_Aires')

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return

        with open(self.signal_file, 'r') as file:
            content = file.read()

            # Find the last entry by splitting the content
        entries = content.strip().split("========================================")
        last_entry = entries[-1].strip()

        # Extract the summary and body using regular expressions
        summary_match = re.search(r'Summary:\s*(.*)', last_entry)
        body_match = re.search(r'Body:\s*(.*)', last_entry)

        if not summary_match and not body_match:
            return

        summary = summary_match.group(1)
        body = body_match.group(1)

        current_time_local = datetime.now(self.local_tz)
        formatted_time = current_time_local.strftime('%Y-%m-%d %H:%M:%S')
        print(f"\n\n=====================\n{formatted_time}\nSummary: {summary}\nBody: {body}\n\n")
        NotificationHandler.send_kde_notification(summary, body)
        play_sound('/usr/share/sounds/Oxygen-Im-Phone-Ring.ogg')  # Specify the path to your sound file

    @staticmethod
    def send_kde_notification(summary, body='', actions=[], replaces_id=0):
        # Get the session bus
        bus = dbus.SessionBus()
        # Get the notification object
        obj = bus.get_object('org.freedesktop.Notifications', '/org/freedesktop/Notifications')
        # Get the interface
        interface = dbus.Interface(obj, dbus_interface='org.freedesktop.Notifications')
        # Send the notification
        notification_id = interface.Notify("GLK Trading", replaces_id, "dialog-information", summary, body, actions, {},
                                           10000)


def main():
    event_handler = NotificationHandler()
    observer = Observer()
    observer.schedule(event_handler, NotificationHandler.signal_file, recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
