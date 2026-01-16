import asyncio
from lib.provider_helper import get_providers_from_list
from lib import config


class BaseMonitorProvider:
    def __init__(self, cfg: config.MonitorProvider, global_cfg: config.Config):
        self.cfg = cfg
        self.global_cfg = global_cfg

        self.notification_providers, self.log_providers = get_providers_from_list(self.cfg.notifications)

    async def load(self):
        pass  # Not required but will always be called. Used for pre-processing prior to first monitor scan.

    async def run_once(self):
        raise NotImplementedError("This Provider Does not Implement run_once")
