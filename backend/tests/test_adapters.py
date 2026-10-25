from app.collectors.platform import get_platform_adapter


def test_platform_adapter_returns_host_metrics_and_capabilities():
    adapter = get_platform_adapter()
    metrics = adapter.system_metrics()
    capabilities = adapter.capabilities()
    assert metrics.platform in {"Windows", "Linux"}
    assert metrics.hostname
    assert metrics.cpu_percent is None or 0 <= metrics.cpu_percent <= 100
    assert metrics.memory_total is None or metrics.memory_total > 0
    assert isinstance(metrics.disks, list)
    assert isinstance(metrics.networks, list)
    assert capabilities["platform"] in {"windows", "linux"}


def test_process_inspection_returns_shared_fields():
    processes = get_platform_adapter().processes()
    assert isinstance(processes, list)
    assert processes
    for process in processes[:5]:
        assert {"pid", "name", "username", "executable", "command_line", "parent_pid", "status",
                "memory_bytes", "cpu_percent"} <= process.keys()
