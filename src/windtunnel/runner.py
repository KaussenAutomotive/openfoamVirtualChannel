"""Ausfuehrung der OpenFOAM-Pipeline in WSL."""
from __future__ import annotations

import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal

from .results import ResidualParser
from .settings import TunnelSettings, WslConfig

STEPS = ["Fall kopieren", "blockMesh", "decomposePar", "snappyHexMesh", "checkMesh", "renumberMesh",
         "decomposeFields", "potentialFoam", "foamRun", "foamToVTK", "Ergebnisse zurueckkopieren"]

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def to_wsl_path(p: Path) -> str:
    p = p.resolve()
    drive = p.drive.rstrip(":").lower()
    rest = "/".join(p.parts[1:])
    return f"/mnt/{drive}/{rest}"


def check_wsl(cfg: WslConfig) -> tuple[bool, str]:
    cmd = ["wsl.exe", "-d", cfg.distro, "--", "bash", "-lc",
           f"source {cfg.bashrc} && command -v foamRun && nproc"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=60, creationflags=_NO_WINDOW)
    except FileNotFoundError:
        return False, "wsl.exe nicht gefunden - ist WSL installiert?"
    except subprocess.TimeoutExpired:
        return False, "Zeitueberschreitung beim Start von WSL."
    text = out.stdout.decode("utf-8", "replace").strip().splitlines()
    if out.returncode != 0 or len(text) < 2 or not text[-1].strip().isdigit():
        err = (out.stderr or out.stdout).decode("utf-16-le" if b"\x00" in (out.stderr or b"") else "utf-8",
                                                 "replace").strip()
        return False, f"OpenFOAM-Umgebung nicht nutzbar: {err or 'unbekannter Fehler'}"
    return True, f"OpenFOAM gefunden ({text[-2].strip()}), {text[-1].strip()} CPU-Kerne in WSL."


class WslRunner(QObject):
    log = Signal(str)
    step = Signal(str, int, int)  # Name, Index, Gesamt
    residual = Signal(int, str, float)
    progress = Signal(int, int)  # Iteration, Gesamt
    finished = Signal(bool, str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._proc: QProcess | None = None
        self._buf = ""
        self._parser = ResidualParser()
        self._settings: TunnelSettings | None = None
        self._failed: str | None = None
        self._done = False
        self._cancelled = False
        self._step = ""
        self._last_it = -1

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning

    def start(self, run_script: Path, settings: TunnelSettings) -> None:
        if self.running:
            return
        self._settings = settings
        self._parser = ResidualParser()
        self._buf, self._failed, self._done, self._cancelled = "", None, False, False
        self._step = ""
        self._last_it = -1
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(self._on_output)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self._proc = proc
        proc.start("wsl.exe", ["-d", settings.wsl.distro, "--", "bash", to_wsl_path(run_script)])

    def cancel(self) -> None:
        if not self.running or self._settings is None:
            return
        self._cancelled = True
        name = self._settings.case_name
        script = f"pid=$(cat /tmp/windtunnel_{name}.pid); pkill -TERM -P $pid; kill $pid"
        try:
            subprocess.run(["wsl.exe", "-d", self._settings.wsl.distro, "--", "bash", "-c", script],
                           timeout=20, creationflags=_NO_WINDOW)
        except (subprocess.SubprocessError, OSError):
            pass
        if self.running:
            self._proc.kill()

    def _on_output(self) -> None:
        data = bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
        self._buf += data.replace("\r\n", "\n").replace("\r", "\n")
        *lines, self._buf = self._buf.split("\n")
        for line in lines:
            self._handle_line(line)

    def _handle_line(self, line: str) -> None:
        self.log.emit(line)
        if line.startswith("##STEP "):
            name = line[7:].strip()
            self._step = name
            idx = STEPS.index(name) + 1 if name in STEPS else 0
            self.step.emit(name, idx, len(STEPS))
        elif line.startswith("##FAIL"):
            self._failed = line[6:].strip() or "Fehler"
        elif line.startswith("##DONE"):
            self._done = True
        res = self._parser.feed(line) if self._step == "foamRun" else None
        if res is not None:
            it, name, val = res
            self.residual.emit(it, name, val)
            if self._settings and it != self._last_it:
                self._last_it = it
                self.progress.emit(it, self._settings.n_iterations)

    def _on_finished(self, code: int, _status) -> None:
        if self._buf:
            self._handle_line(self._buf)
            self._buf = ""
        if self._cancelled:
            self.finished.emit(False, "Abgebrochen.")
        elif self._done and not self._failed:
            self.finished.emit(True, "Simulation abgeschlossen.")
        else:
            self.finished.emit(False, self._failed or f"Prozess beendet (Exit {code}).")

    def _on_error(self, err) -> None:
        if err == QProcess.ProcessError.FailedToStart:
            self.finished.emit(False, "wsl.exe konnte nicht gestartet werden.")
