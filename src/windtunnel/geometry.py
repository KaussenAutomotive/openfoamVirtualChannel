"""Aufbereitung des Fahrzeugnetzes: Einheiten, Ausrichtung, Kennwerte, STL-Export."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyvista as pv
import vtk

UNIT_CHOICES = {"Millimeter": 1e-3, "Zentimeter": 1e-2, "Meter": 1.0, "Zoll": 0.0254}
NATIVE_UNIT_LABEL = {"millimeter": "Millimeter", "centimeter": "Zentimeter", "meter": "Meter",
                     "inch": "Zoll", "micron": "Millimeter", "foot": "Meter"}


def guess_unit_label(mesh: pv.PolyData) -> str:
    """Fahrzeuge sind 2-8 m lang; liegt die Laenge darueber, ist es Millimeter."""
    length = max(mesh.bounds[1] - mesh.bounds[0], mesh.bounds[3] - mesh.bounds[2],
                 mesh.bounds[5] - mesh.bounds[4])
    if length > 100:
        return "Millimeter"
    if length > 20:
        return "Zentimeter"
    return "Meter"


@dataclass
class Placement:
    scale: float = 1.0
    rot_x: float = 0.0
    rot_y: float = 0.0
    rot_z: float = 0.0
    ground_clearance: float = 0.0  # m


def place_vehicle(raw: pv.PolyData, p: Placement) -> pv.PolyData:
    """Skalieren, drehen, auf den Boden (z=0) stellen, in y zentrieren, x-Mitte auf 0."""
    mesh = raw.copy()
    mesh.points = mesh.points * p.scale
    for axis, angle in (("x", p.rot_x), ("y", p.rot_y), ("z", p.rot_z)):
        if angle:
            mesh = getattr(mesh, f"rotate_{axis}")(angle, point=(0, 0, 0), inplace=False)
    b = mesh.bounds
    shift = np.array([-(b[0] + b[1]) / 2, -(b[2] + b[3]) / 2, -b[4] + p.ground_clearance])
    mesh.points = mesh.points + shift
    return mesh


@dataclass
class VehicleInfo:
    length: float
    width: float
    height: float
    frontal_area: float
    surface_area: float
    n_triangles: int
    open_edges: int
    bounds: tuple[float, ...]

    @property
    def watertight(self) -> bool:
        return self.open_edges == 0


def projected_area(mesh: pv.PolyData, n: int = 180) -> float:
    """Stirnflaeche in Anstroemrichtung (+x) durch Strahlverfolgung auf einem y-z-Raster."""
    b = mesh.bounds
    ys = np.linspace(b[2], b[3], n)
    zs = np.linspace(b[4], b[5], max(8, int(n * (b[5] - b[4]) / max(b[3] - b[2], 1e-9))))
    loc = vtk.vtkStaticCellLocator()
    loc.SetDataSet(mesh)
    loc.BuildLocator()
    t, x, pc = vtk.mutable(0.0), [0.0] * 3, [0.0] * 3
    sub = vtk.mutable(0)
    hits = 0
    for y in ys:
        for z in zs:
            if loc.IntersectWithLine((b[0] - 1.0, y, z), (b[1] + 1.0, y, z), 1e-6, t, x, pc, sub):
                hits += 1
    dy = (b[3] - b[2]) / (len(ys) - 1)
    dz = (b[5] - b[4]) / (len(zs) - 1)
    return hits * dy * dz


def analyze(mesh: pv.PolyData) -> VehicleInfo:
    b = mesh.bounds
    open_edges = mesh.extract_feature_edges(
        boundary_edges=True, feature_edges=False, manifold_edges=False, non_manifold_edges=False
    ).n_cells
    return VehicleInfo(
        length=b[1] - b[0],
        width=b[3] - b[2],
        height=b[5] - b[4],
        frontal_area=projected_area(mesh),
        surface_area=float(mesh.area),
        n_triangles=mesh.n_cells,
        open_edges=int(open_edges),
        bounds=tuple(b),
    )


def export_stl(mesh: pv.PolyData, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mesh.save(str(path), binary=True)
