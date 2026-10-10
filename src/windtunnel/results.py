"""Auswertung der OpenFOAM-Ergebnisse (Beiwerte, Residuen, VTK-Dateien)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pyvista as pv

_RESIDUAL_RE = re.compile(r"Solving for (\w+), Initial residual = ([0-9.eE+-]+)")
_TIME_RE = re.compile(r"^Time = ([0-9.eE+-]+)s?\s*$")


@dataclass
class Coefficients:
    time: np.ndarray
    values: dict[str, np.ndarray]

    @property
    def cd(self) -> float | None:
        return _last(self.values.get("Cd"))

    @property
    def cl(self) -> float | None:
        return _last(self.values.get("Cl"))

    def averaged(self, name: str, fraction: float = 0.2) -> float | None:
        """Mittel ueber den letzten Anteil der Iterationen (glaettet Oszillationen)."""
        v = self.values.get(name)
        if v is None or len(v) == 0:
            return None
        n = max(1, int(len(v) * fraction))
        return float(np.mean(v[-n:]))


def _last(a: np.ndarray | None) -> float | None:
    return None if a is None or len(a) == 0 else float(a[-1])


def parse_coefficients(results_dir: Path) -> Coefficients | None:
    files = sorted((results_dir / "postProcessing" / "forceCoeffs").glob("*/forceCoeffs.dat"))
    if not files:
        return None
    columns: list[str] = []
    rows: list[list[float]] = []
    for f in files:  # mehrere Startzeiten nacheinander
        for line in f.read_text(errors="replace").splitlines():
            if line.startswith("#"):
                parts = line[1:].split()
                if parts and parts[0] == "Time":
                    columns = parts
                continue
            parts = line.split()
            if columns and len(parts) == len(columns):
                try:
                    rows.append([float(x) for x in parts])
                except ValueError:
                    pass
    if not rows:
        return None
    data = np.array(rows)
    return Coefficients(data[:, 0], {c: data[:, i] for i, c in enumerate(columns) if i > 0})


@dataclass
class ResidualParser:
    """Liest Residuen live aus dem foamRun-Log."""

    iteration: int = 0
    history: dict[str, list[tuple[int, float]]] = field(default_factory=dict)
    _seen: set[str] = field(default_factory=set)

    def feed(self, line: str) -> tuple[int, str, float] | None:
        m = _TIME_RE.match(line.strip())
        if m:
            self.iteration = int(float(m.group(1)))
            self._seen.clear()
            return None
        m = _RESIDUAL_RE.search(line)
        if m and m.group(1) not in self._seen:
            self._seen.add(m.group(1))  # nur erster Korrektor je Iteration
            field_name, value = m.group(1), float(m.group(2))
            self.history.setdefault(field_name, []).append((self.iteration, value))
            return self.iteration, field_name, value
        return None


def _numeric_dirs(path: Path) -> list[Path]:
    out = []
    if path.is_dir():
        for d in path.iterdir():
            try:
                float(d.name)
                out.append(d)
            except ValueError:
                pass
    return sorted(out, key=lambda d: float(d.name))


def _latest_files(base: Path, pattern: str) -> list[Path]:
    dirs = _numeric_dirs(base)
    return sorted(dirs[-1].glob(pattern)) if dirs else []


@dataclass
class FlowResults:
    surface: pv.PolyData | None
    slices: dict[str, pv.PolyData]
    streamlines: pv.PolyData | None
    coefficients: Coefficients | None
    speed: float
    iteration: str = ""


def load_results(results_dir: Path, speed_ms: float) -> FlowResults:
    surface = None
    vtk_files = sorted((results_dir / "VTK").glob("*vehicle*.vtk")) if (results_dir / "VTK").is_dir() else []
    if vtk_files:
        blocks = [pv.read(str(f)) for f in vtk_files]
        surface = pv.merge(blocks, merge_points=False).extract_surface(algorithm=None) \
            if len(blocks) > 1 else blocks[0]
        if "p" in surface.point_data:
            surface.point_data["cp"] = surface.point_data["p"] / (0.5 * speed_ms**2)
        elif "p" in surface.cell_data:
            surface.cell_data["cp"] = surface.cell_data["p"] / (0.5 * speed_ms**2)
        for data in (surface.point_data, surface.cell_data):
            if "U" in data:
                data["Umag"] = np.linalg.norm(data["U"], axis=1)

    pp = results_dir / "postProcessing"
    slices: dict[str, pv.PolyData] = {}
    for f in _latest_files(pp / "cutPlane", "*.vtk"):
        s = pv.read(str(f))
        if "U" in s.point_data:
            s.point_data["Umag"] = np.linalg.norm(s.point_data["U"], axis=1)
        if "p" in s.point_data:
            s.point_data["cp"] = s.point_data["p"] / (0.5 * speed_ms**2)
        slices[f.stem] = s

    tracks = []
    for d in sorted(pp.glob("streamlines*")) if pp.is_dir() else []:
        for f in _latest_files(d, "*.vtk"):
            t = pv.read(str(f))
            if t.n_points:
                if "U" in t.point_data:
                    t.point_data["Umag"] = np.linalg.norm(t.point_data["U"], axis=1)
                tracks.append(t)
    streams = pv.merge(tracks) if len(tracks) > 1 else (tracks[0] if tracks else None)

    latest = _numeric_dirs(pp / "cutPlane")
    return FlowResults(surface, slices, streams, parse_coefficients(results_dir), speed_ms,
                       latest[-1].name if latest else "")
