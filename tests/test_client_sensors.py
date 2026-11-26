from datetime import datetime, timezone
from types import SimpleNamespace


from marathon.client.availability import Sample
from marathon.client.sensors import SystemSensors, windows_idle_seconds


class FakePs:
    class NoSuchProcess(Exception):
        pass

    class AccessDenied(Exception):
        pass

    def cpu_percent(self, interval=None):
        assert interval is None
        return 12.5

    def virtual_memory(self):
        return SimpleNamespace(percent=45.0)

    def process_iter(self, attrs):
        assert attrs == ["name"]
        yield SimpleNamespace(info={"name": "editor.exe"})
        yield SimpleNamespace(info={"name": None})
        yield SimpleNamespace(info={"name": "renderer.exe"})


class AccessDeniedProcess:
    @property
    def info(self):
        raise FakePs.AccessDenied


def test_sample_reads_metrics_and_skips_unavailable_process_names():
    class Ps(FakePs):
        def process_iter(self, attrs):
            yield SimpleNamespace(info={"name": "editor.exe"})
            yield SimpleNamespace(info={"name": ""})
            yield AccessDeniedProcess()
            yield SimpleNamespace(info={"name": "renderer.exe"})

    result = SystemSensors(
        ps=Ps(),
        run=lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="4\n81\n"),
        monotonic=lambda: 10.0,
        idle_reader=lambda: 123.0,
    ).sample(datetime(2025, 1, 2, tzinfo=timezone.utc))

    assert isinstance(result, Sample)
    assert result.cpu_percent == 12.5
    assert result.ram_percent == 45.0
    assert result.gpu_percent == 81.0
    assert result.idle_seconds == 123.0
    assert result.processes == ("editor.exe", "renderer.exe")


def test_gpu_failure_is_cached_until_retry_interval_then_retried():
    class Clock:
        value = 0.0

        def __call__(self):
            return self.value

    clock = Clock()
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        raise FileNotFoundError

    sensors = SystemSensors(
        ps=FakePs(), run=run, monotonic=clock, gpu_retry_s=600,
        idle_reader=lambda: None,
    )
    now = datetime(2025, 1, 2, tzinfo=timezone.utc)
    assert sensors.sample(now).gpu_percent is None
    clock.value = 599.9
    assert sensors.sample(now).gpu_percent is None
    assert len(calls) == 1
    clock.value = 600.0
    assert sensors.sample(now).gpu_percent is None
    assert len(calls) == 2
    assert calls[0][0][0] == ("nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits")
    assert calls[0][1]["capture_output"] is True
    assert calls[0][1]["text"] is True
    assert calls[0][1]["timeout"] == 2.0
    assert calls[0][1]["creationflags"] == 0


def test_windows_idle_returns_none_on_non_windows():
    assert windows_idle_seconds() is None
