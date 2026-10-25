from .platform import get_platform_adapter

_adapter = get_platform_adapter()


def collect_processes():
    return _adapter.processes()
