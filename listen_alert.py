import time
from datetime import datetime
import pytz
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import pygame

# Initialize pygame mixer
pygame.mixer.init()


# Function to play sound
def play_sound(sound_file):
    sound = pygame.mixer.Sound(sound_file)
    sound.play()


# Custom event handler class
class MyHandler(FileSystemEventHandler):
    signal_file = "/tmp/hummingbot.txt"

    def on_modified(self, event):
        if event.is_directory:
            return

        local_tz = pytz.timezone('America/Buenos_Aires')
        current_time_local = datetime.now(local_tz)
        formatted_time = current_time_local.strftime('%Y-%m-%d %H:%M:%S')
        with open(self.signal_file, 'r') as file:
            content = file.read()
        print(f"\n\n=====================\n{formatted_time}\n{content}")

        play_sound('/usr/share/sounds/Oxygen-Im-Phone-Ring.ogg')  # Specify the path to your sound file


def main():

    event_handler = MyHandler()
    observer = Observer()
    observer.schedule(event_handler, MyHandler.signal_file, recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
