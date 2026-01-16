from providers.notification.Base.main import BaseNotificationProvider
from lib import config


class NtfyNotificationProvider(BaseNotificationProvider):
    def __init__(self, cfg: config.NotificationBlock):
        super().__init__(cfg)
