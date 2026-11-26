from pathlib import Path

from marathon.client.local_state import VersionState, load_version_state, save_version_state, version_exe
from marathon.client.starter import exit_restart, run_starter


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeProcess:
    def __init__(self, clock, exit_code, runtime):
        self.clock = clock
        self.exit_code = exit_code
        self.runtime = runtime

    def wait(self):
        self.clock.now += self.runtime
        return self.exit_code


def test_starter_exit_zero_returns_zero(tmp_path):
    save_version_state(tmp_path, VersionState("1.0.0"))
    exe = version_exe(tmp_path, "1.0.0")
    exe.parent.mkdir(parents=True)
    exe.touch()
    clock = FakeClock()
    started = []

    def spawn(args):
        started.append(args)
        return FakeProcess(clock, 0, 1)

    assert run_starter(tmp_path, spawn, clock.monotonic, clock.sleep) == 0
    assert started == [[str(exe), "--run"]]
    assert clock.sleeps == []


def test_restart_exit_reloads_version_and_starts_new_executable(tmp_path):
    save_version_state(tmp_path, VersionState("1.0.0"))
    old_exe = version_exe(tmp_path, "1.0.0")
    new_exe = version_exe(tmp_path, "2.0.0")
    for exe in (old_exe, new_exe):
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.touch()
    clock = FakeClock()
    calls = []

    def spawn(args):
        calls.append(args)
        if len(calls) == 1:
            save_version_state(tmp_path, VersionState("2.0.0", "1.0.0", True))
            return FakeProcess(clock, exit_restart, 2)
        return FakeProcess(clock, 0, 1)

    assert run_starter(tmp_path, spawn, clock.monotonic, clock.sleep) == 0
    assert calls == [[str(old_exe), "--run"], [str(new_exe), "--run"]]
    assert clock.sleeps == []


def test_pending_fast_crash_rolls_back_and_marks_version_failed(tmp_path, caplog):
    save_version_state(tmp_path, VersionState("2.0.0", "1.0.0", True, ["2.0.0"]))
    for version in ("1.0.0", "2.0.0"):
        exe = version_exe(tmp_path, version)
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.touch()
    clock = FakeClock()
    calls = []

    def spawn(args):
        calls.append(args)
        return FakeProcess(clock, 1 if len(calls) == 1 else 0, 2)

    assert run_starter(tmp_path, spawn, clock.monotonic, clock.sleep) == 0
    state = load_version_state(tmp_path)
    assert state == VersionState("1.0.0", None, False, ["2.0.0"])
    assert calls == [[str(version_exe(tmp_path, "2.0.0")), "--run"],
                     [str(version_exe(tmp_path, "1.0.0")), "--run"]]
    assert "Update auf 2.0.0 fehlgeschlagen, zurück auf 1.0.0" in caplog.text


def test_crash_backoff_increases_and_resets_after_healthy_run(tmp_path):
    save_version_state(tmp_path, VersionState("1.0.0"))
    exe = version_exe(tmp_path, "1.0.0")
    exe.parent.mkdir(parents=True)
    exe.touch()
    clock = FakeClock()
    runtimes = iter((1, 1, 1, 60, 1, 1))
    exits = iter((1, 1, 1, 1, 1, 0))

    def spawn(args):
        return FakeProcess(clock, next(exits), next(runtimes))

    assert run_starter(tmp_path, spawn, clock.monotonic, clock.sleep) == 0
    assert clock.sleeps == [1, 2, 5, 1, 2]


def test_missing_state_returns_two(tmp_path):
    assert run_starter(tmp_path, lambda args: None) == 2


def test_missing_current_executable_rolls_back_to_previous(tmp_path):
    save_version_state(tmp_path, VersionState("2.0.0", "1.0.0", True))
    previous_exe = version_exe(tmp_path, "1.0.0")
    previous_exe.parent.mkdir(parents=True)
    previous_exe.touch()
    clock = FakeClock()
    started = []

    def spawn(args):
        started.append(args)
        return FakeProcess(clock, 0, 1)

    assert run_starter(tmp_path, spawn, clock.monotonic, clock.sleep) == 0
    assert load_version_state(tmp_path) == VersionState("1.0.0", None, False, ["2.0.0"])
    assert started == [[str(previous_exe), "--run"]]
