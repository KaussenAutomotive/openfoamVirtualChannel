"""CPU- und Speicherauslastung der WSL-VM (dort laeuft OpenFOAM)."""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal

from .runner import to_wsl_path

_SCRIPT = """#!/bin/bash
while true; do
    grep '^cpu' /proc/stat || exit
    grep -E '^(MemTotal|MemAvailable)' /proc/meminfo || exit
    echo --- || exit
    sleep 1
done
"""


@dataclass
class CpuSample:
    total: float  # Prozent
    cores: list[float]  # Prozent je logischem Kern
    mem_used_gb: float
    mem_total_gb: float


class CpuSampler:
    """Berechnet Auslastungen aus aufeinanderfolgenden /proc/stat- und /proc/meminfo-Ausgaben."""

    def __init__(self) -> None:
        self._prev: dict[str, tuple[int, int]] = {}

    def update(self, lines: list[str]) -> CpuSample | None:
        cur: dict[str, tuple[int, int]] = {}
        mem: dict[str, int] = {}
        for line in lines:
            parts = line.split()
            if not parts:
                continue
            if parts[0].startswith("cpu"):
                vals = [int(v) for v in parts[1:]]
                if len(vals) < 5:
                    continue
                idle = vals[3] + vals[4]  # idle + iowait
                cur[parts[0]] = (sum(vals[:8]), idle)
            elif parts[0] in ("MemTotal:", "MemAvailable:") and len(parts) >= 2:
                mem[parts[0]] = int(parts[1])
        prev, self._prev = self._prev, cur
        if "cpu" not in cur or "cpu" not in prev or "MemTotal:" not in mem:
            return None

        def pct(name: str) -> float:
            t1, i1 = cur[name]
            t0, i0 = prev.get(name, (t1, i1))
            dt = t1 - t0
            return 0.0 if dt <= 0 else 100.0 * (1.0 - (i1 - i0) / dt)

        cores = sorted((n for n in cur if n != "cpu"), key=lambda n: int(n[3:]))
        total_kb = mem["MemTotal:"]
        used_kb = total_kb - mem.get("MemAvailable:", total_kb)
        return CpuSample(pct("cpu"), [pct(n) for n in cores], used_kb / 1048576, total_kb / 1048576)


class SystemMonitor(QObject):
    sample = Signal(object)  # CpuSample
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._proc: QProcess | None = None
        self._sampler = CpuSampler()
        self._buf = ""
        self._lines: list[str] = []

    @property
    def active(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning

    def start(self, distro: str) -> None:
        if self.active:
            return
        script = Path(tempfile.gettempdir()) / "windtunnel_monitor.sh"
        script.write_text(_SCRIPT, encoding="utf-8", newline="\n")
        self._sampler, self._buf, self._lines = CpuSampler(), "", []
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(self._on_output)
        proc.errorOccurred.connect(lambda _e: self.failed.emit("Auslastungsanzeige konnte nicht starten."))
        self._proc = proc
        proc.start("wsl.exe", ["-d", distro, "--", "bash", to_wsl_path(script)])

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.readyReadStandardOutput.disconnect()
            self._proc.kill()
            self._proc.waitForFinished(2000)
            self._proc = None

    def _on_output(self) -> None:
        self._buf += bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
        *lines, self._buf = self._buf.replace("\r", "").split("\n")
        for line in lines:
            if line.strip() == "---":
                s = self._sampler.update(self._lines)
                self._lines = []
                if s is not None:
                    self.sample.emit(s)
            else:
                self._lines.append(line)
