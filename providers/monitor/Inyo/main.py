from datetime import UTC, datetime

from lib import config
from lib.aes_token import AESCipher, ConfidentialTokenService
from lib.data_manager import AsyncAutoSavingDict
from providers.monitor.Base.main import BaseMonitorProvider
from providers.monitor.Base.tools.Statistics.lib.common import init_sqlite_db
from providers.monitor.Base.tools.Statistics.lib.stock_event_aggregator import (
    StockEventAggregator,
)

from .differ import PermitAvailabilityDiffer
from .event_builder import InyoEventBuilder
from .http import *

INYO_AVAILABILITY_API = "https://www.recreation.gov/api/permitinyo/{target_id}/availabilityv2"


class InyoMonitorProvider(BaseMonitorProvider):
    def __init__(self, cfg: config.MonitorProvider, global_cfg: config.Config):
        super().__init__(cfg, global_cfg)

        self.data = AsyncAutoSavingDict(cfg.raw['data_dir'], cfg.raw['data_file'])
        self.differ = PermitAvailabilityDiffer(self.data, permit_lookup=cfg.raw['permit_codes'])

        self.token_service = ConfidentialTokenService()
        self.token_service.add_key(0, key=AESCipher.derive_key_from_secret(global_cfg.secret_key))
        self.builder = InyoEventBuilder(
            version="2.0.0",
            auto_reserve_base_url=f"{global_cfg.service.base_url}"
                                  f"{global_cfg.monitor.providers['Inyo'].tools['InyoATC']['url_prefix']}"
                                  f"/atc" if (len(cfg.tools) > 0) else None,
            aes_token_service=self.token_service,
            token_kid=0,
            token_ttl_seconds=cfg.tools['InyoATC']['atc_validity_duration'] if (len(cfg.tools) > 0) else None,
            # type: ignore[arg-type]
            max_permits_per_click=cfg.tools['InyoATC']['max_permits'] if (len(cfg.tools) > 0) else None
            # type: ignore[arg-type]
        )
        # ---- Statistics DB ---------------------------------------
        # Ensure SQLite engine is ready in this process. Blueprint init
        # happens in the web process, so we repeat the call here (idempotent).
        init_sqlite_db(self.global_cfg.extra["sqlite"]["path"])
        # Event aggregator shared with Statistics tool (requires DB ready)
        self.aggregator = StockEventAggregator(source="Inyo", window_hours=24)

        self._construct_fetch_ranges()

    def _construct_fetch_ranges(self):
        self.ranges = {}
        self.range_mapping = {}
        for target in self.cfg.targets:
            self.ranges[INYO_AVAILABILITY_API.format(target_id=target)] = self.cfg.targets[target].complete_months()
            self.range_mapping[INYO_AVAILABILITY_API.format(target_id=target)] = target

    async def load(self):
        await self.data.load()

    async def run_once(self):
        now_utc = datetime.now(UTC)
        # Remove expired active events before processing new data
        self.aggregator.purge_expired(now_utc)

        gathered_data, errors = await fetch_ranges_concurrently(self.ranges)

        # remove dates that don't match filters per target
        for url in gathered_data.keys():
            target = self.range_mapping[url]
            for date_key in list(gathered_data[url].keys()):
                if not self.cfg.targets[target].date_matches(date_key):
                    del gathered_data[url][date_key]

        changes = self.differ.diff(gathered_data)
        if not changes:
            return

        # process changes per target
        for dataset in changes:
            target = self.range_mapping[dataset["source"]]
            try:
                for day in dataset["dates"]:
                    date_iso = day["date"]
                    for perm in day["permits"]:
                        code = perm["code"]
                        name = perm.get("name") or code
                        new_remaining = perm["new_remaining"]

                        # Feed observation to aggregator (handles grouping & DB)
                        self.aggregator.add_observation(
                            code=code,
                            name=name,
                            date_iso=date_iso,
                            count=new_remaining,
                            timestamp=now_utc,
                        )

                        # temp for debugging
                        print(f"PERMIT: {name} DATE {date_iso} COUNT: {new_remaining}")
            except Exception as e:
                for lp in self.log_providers:
                    lp.send_notification(f"Statistics aggregation failed: {e}")

            # ----- Continue with notifications -----------------
            for provider in self.notification_providers:
                data = self.builder.build(dataset["dates"], target, provider)
                provider.send_notification(data)
