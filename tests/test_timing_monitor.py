import pytest

from windtunnel.monitor import CpuSampler
from windtunnel.settings import TunnelSettings
from windtunnel.timing import RunTimer, format_duration


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_format_duration():
    assert format_duration(3725) == "01:02:05"
    assert format_duration(None) == "--:--:--"


def test_eta_from_iteration_rate():
    clock = FakeClock()
    t = RunTimer(clock=clock)
    t.start()
    assert t.eta() is None
    for i in range(1, 11):
        clock.t += 2.0  # 2 s pro Iteration
        t.update_iteration(i, 110)
    assert t.seconds_per_iteration() == pytest.approx(2.0)
    assert t.eta() == pytest.approx(200.0)
    assert t.elapsed == pytest.approx(20.0)
    t.stop()
    clock.t += 50
    assert t.elapsed == pytest.approx(20.0)


def _stat(cpu_busy, cpu_idle, core_busy, core_idle):
    return [
        f"cpu  {cpu_busy} 0 0 {cpu_idle} 0 0 0 0 0 0",
        f"cpu0 {core_busy} 0 0 {core_idle} 0 0 0 0 0 0",
        f"cpu1 0 0 0 {cpu_idle // 2} 0 0 0 0 0 0",
        "MemTotal:       16777216 kB",
        "MemAvailable:    8388608 kB",
    ]


def test_cpu_sampler_percentages():
    s = CpuSampler()
    assert s.update(_stat(100, 100, 100, 100)) is None  # erste Messung ohne Differenz
    out = s.update(_stat(175, 125, 175, 125))
    assert out.total == pytest.approx(75.0)
    assert out.cores[0] == pytest.approx(75.0)
    assert len(out.cores) == 2
    assert out.mem_total_gb == pytest.approx(16.0)
    assert out.mem_used_gb == pytest.approx(8.0)


def test_water_medium_viscosity():
    assert TunnelSettings(medium="Wasser").nu == pytest.approx(1.0e-6)
    assert TunnelSettings().nu == pytest.approx(1.5e-5)
