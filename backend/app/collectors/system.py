from .platform import get_platform_adapter

_adapter = get_platform_adapter()


def collect_system_metrics():
    return _adapter.system_metrics()


def platform_capabilities():
    return _adapter.capabilities()
