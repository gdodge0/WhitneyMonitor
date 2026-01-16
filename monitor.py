import asyncio
from lib import config
from lib.provider_helper import load_monitor

cfg = config.Config.from_yaml('conf.yaml')


async def exec_monitor(monitor_cfg: config.MonitorProvider, cfg: config.Config):
    monitor = await load_monitor(monitor_cfg, cfg)
    await monitor.load()
    while True:
        await monitor.run_once()
        await asyncio.sleep(monitor_cfg.cooldown)


async def main():
    for monitor in cfg.monitor.providers:
        monitor_tasks = set()
        monitor_tasks.add(asyncio.create_task(exec_monitor(cfg.monitor.providers[monitor], cfg)))

        await asyncio.gather(*monitor_tasks)


if __name__ == "__main__":
    asyncio.run(main())
