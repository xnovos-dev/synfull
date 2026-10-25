import platform

from .linux import LinuxAdapter
from .windows import WindowsAdapter


def get_platform_adapter():
    system = platform.system()
    if system == "Windows":
        return WindowsAdapter()
    if system == "Linux":
        return LinuxAdapter()
    raise RuntimeError(f"Unsupported operating system: {system}")
