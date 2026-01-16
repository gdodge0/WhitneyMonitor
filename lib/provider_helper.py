import importlib
from lib import config


def get_providers_from_list(providers_cfg: dict):
    providers = []
    log_providers = []
    for provider_name in providers_cfg:
        if not provider_name:
            raise ValueError('Provider Name cannot be empty')

        module_path = f"providers.notification.{provider_name.title()}.main"
        class_name = f"{provider_name.title()}NotificationProvider"
        try:
            module = importlib.import_module(module_path)
            provider_class = getattr(module, class_name)

            provider = provider_class(providers_cfg[provider_name])
            providers.append(provider)
            if provider.cfg['enable_logs']:
                log_providers.append(provider)
        except (ImportError, AttributeError) as e:
            raise ImportError(f"Could not load provider '{provider_name}': {e}")

    return providers, log_providers


async def load_monitor(monitor_cfg: config.MonitorProvider, global_cfg: config.Config):
    name = monitor_cfg.name
    module_path = f"providers.monitor.{name}.main"
    class_name = f"{name}MonitorProvider"

    try:
        module = importlib.import_module(module_path)
        monitor_class = getattr(module, class_name)

        provider = monitor_class(monitor_cfg, global_cfg)

    except (ImportError, AttributeError) as e:
        raise ImportError(f"Could not load provider '{name}': {e}")

    return provider
