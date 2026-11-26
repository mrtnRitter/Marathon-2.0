from datetime import datetime, timedelta

from marathon.client.availability import AvailabilityEvaluator, Sample


def sample(
    seconds=0,
    *,
    idle_seconds=0.0,
    cpu=5.0,
    ram=20.0,
    gpu=None,
    processes=(),
    start=None,
):
    start = start or datetime(2026, 11, 25, 9, 0)
    return Sample(
        now=start + timedelta(seconds=seconds),
        idle_seconds=idle_seconds,
        cpu_percent=cpu,
        ram_percent=ram,
        gpu_percent=gpu,
        processes=tuple(processes),
    )


def result(state):
    return state.available, state.reason


def idle_config(**overrides):
    idle = {
        "enabled": True,
        "minutes": 15,
        "max_cpu_percent": 25,
        "max_ram_percent": 75,
        "max_gpu_percent": 20,
    }
    idle.update(overrides)
    return {"idle": idle}


def feed(evaluator, *, start=0, end=900, cpu=5, ram=20, gpu=None, base=None, idle_since=0):
    state = None
    for seconds in range(start, end + 1, 5):
        state = evaluator.evaluate(
            sample(
                seconds,
                idle_seconds=seconds - idle_since,
                cpu=cpu,
                ram=ram,
                gpu=gpu,
                start=base,
            ),
            float(seconds),
        )
    return result(state)


def test_empty_configuration_is_always_available():
    evaluator = AvailabilityEvaluator({})
    for seconds in range(0, 60, 5):
        assert result(evaluator.evaluate(sample(seconds), float(seconds))) == (True, "")
    assert evaluator.activated is False


def test_outside_schedule_blocks_and_resets_activation():
    config = idle_config()
    config["schedule"] = {
        "enabled": True,
        "days": {"wed": [{"start": "09:00", "end": "09:16"}]},
    }
    evaluator = AvailabilityEvaluator(config)
    assert feed(evaluator) == (True, "")
    assert evaluator.activated
    state = evaluator.evaluate(sample(960, idle_seconds=960), 960.0)
    assert result(state) == (False, "Außerhalb des Zeitplans")
    assert evaluator.activated is False


def test_blocked_process_matches_case_and_exe_before_load_checks():
    evaluator = AvailabilityEvaluator(
        {"blocked_processes": ["RenderTool.exe"], **idle_config()}
    )
    state = evaluator.evaluate(sample(cpu=99, processes=("rendertool",)), 0.0)
    assert result(state) == (False, "Programm RenderTool.exe aktiv")
    assert evaluator.activated is False


def test_idle_waiting_and_recent_input_reasons():
    evaluator = AvailabilityEvaluator(idle_config())
    assert result(evaluator.evaluate(sample(idle_seconds=120), 0.0)) == (
        False,
        "Rechner wird benutzt",
    )

    evaluator = AvailabilityEvaluator(idle_config())
    for seconds in range(0, 301, 5):
        state = evaluator.evaluate(sample(seconds, idle_seconds=900), float(seconds))
    assert result(state) == (False, "Warte auf niedrige Last")


def test_missing_idle_measurement_blocks():
    evaluator = AvailabilityEvaluator(idle_config())
    assert result(evaluator.evaluate(sample(idle_seconds=None), 0.0)) == (
        False,
        "Leerlauf nicht messbar",
    )


def test_high_cpu_and_ram_block_after_history_window():
    evaluator = AvailabilityEvaluator(idle_config())
    assert feed(evaluator, end=900, cpu=40) == (False, "CPU-Last zu hoch (40 %)")

    evaluator = AvailabilityEvaluator(idle_config())
    assert feed(evaluator, end=900, ram=80) == (False, "RAM-Auslastung zu hoch (80 %)")


def test_high_gpu_blocks_but_missing_gpu_values_are_ignored():
    evaluator = AvailabilityEvaluator(idle_config())
    assert feed(evaluator, end=900, gpu=30) == (False, "GPU-Last zu hoch (30 %)")

    evaluator = AvailabilityEvaluator(idle_config())
    assert feed(evaluator, gpu=None) == (True, "")
    assert evaluator.activated


def test_low_load_activates_and_ignores_job_load_until_user_input():
    evaluator = AvailabilityEvaluator(idle_config())
    assert feed(evaluator) == (True, "")
    assert evaluator.activated

    state = evaluator.evaluate(sample(905, idle_seconds=905, cpu=95), 905.0)
    assert result(state) == (True, "")
    assert evaluator.activated

    state = evaluator.evaluate(sample(910, idle_seconds=2, cpu=95), 910.0)
    assert result(state) == (False, "Rechner wird benutzt")
    assert evaluator.activated is False

    assert feed(evaluator, start=915, end=1805, cpu=5, idle_since=910) == (
        False,
        "Rechner wird benutzt",
    )
    assert feed(evaluator, start=1810, end=1810, cpu=5, idle_since=910) == (True, "")
    assert evaluator.activated


def test_schedule_end_during_activation_resets_activation():
    base = datetime(2026, 11, 25, 9, 30)
    config = idle_config()
    config["schedule"] = {
        "enabled": True,
        "days": {"wed": [{"start": "09:00", "end": "09:46"}]},
    }
    evaluator = AvailabilityEvaluator(config)
    assert feed(evaluator, base=base) == (True, "")
    assert evaluator.activated
    state = evaluator.evaluate(sample(960, idle_seconds=960, start=base), 960.0)
    assert result(state) == (False, "Außerhalb des Zeitplans")
    assert evaluator.activated is False


def test_set_config_resets_activation():
    evaluator = AvailabilityEvaluator(idle_config())
    assert feed(evaluator) == (True, "")
    assert evaluator.activated
    evaluator.set_config(idle_config(max_cpu_percent=30))
    assert evaluator.activated is False
    state = evaluator.evaluate(sample(905, idle_seconds=905), 905.0)
    assert result(state) == (True, "")
    assert evaluator.activated


def test_irregular_measurements_retain_window_boundary_and_bound_history():
    interval_s = 5.01
    evaluator = AvailabilityEvaluator(idle_config())
    first_expected_activation = 900 + interval_s
    activation_index = int(first_expected_activation // interval_s) + 1

    for index in range(activation_index + 1):
        monotonic_s = index * interval_s
        state = evaluator.evaluate(
            sample(monotonic_s, idle_seconds=monotonic_s, cpu=5), monotonic_s
        )
    assert monotonic_s >= first_expected_activation
    assert result(state) == (True, "")
    assert evaluator.activated

    total_samples = int(3600 / interval_s)
    for index in range(activation_index + 1, total_samples + 1):
        monotonic_s = index * interval_s
        state = evaluator.evaluate(
            sample(monotonic_s, idle_seconds=monotonic_s, cpu=5), monotonic_s
        )
    assert evaluator.activated
    assert len(evaluator._history) <= idle_config()["idle"]["minutes"] * 60 / 5 + 2
