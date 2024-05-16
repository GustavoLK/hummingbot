import dbus


class Notificator:

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
        # Write the notification message to the file
        with open('/tmp/hummingbot.txt', 'w') as file:
            file.write(f"Summary: {summary}\nBody: {body}")
        return notification_id

    @staticmethod
    def notify(summary, body):
        Notificator.send_kde_notification(summary, body)


