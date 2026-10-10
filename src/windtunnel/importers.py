"""Import von STL, BIN (Binary-STL), STEP/STP und 3MF als pyvista.PolyData."""
from __future__ import annotations

import os
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyvista as pv

SUPPORTED_EXTENSIONS = (".stl", ".bin", ".step", ".stp", ".3mf")
FILE_FILTER = "3D-Geometrie (*.stl *.bin *.step *.stp *.3mf);;Alle Dateien (*)"

UNIT_TO_METER = {
    "micron": 1e-6,
    "millimeter": 1e-3,
    "centimeter": 1e-2,
    "inch": 0.0254,
    "foot": 0.3048,
    "meter": 1.0,
}


class GeometryImportError(Exception):
    pass


@dataclass
class ImportResult:
    mesh: pv.PolyData
    native_unit: str | None  # None = Datei traegt keine Einheit
    source: Path


def load_geometry(path: str | os.PathLike, step_detail: float = 1.0) -> ImportResult:
    p = Path(path)
    ext = p.suffix.lower()
    if not p.is_file():
        raise GeometryImportError(f"Datei nicht gefunden: {p}")
    if ext in (".stl", ".bin"):
        mesh, unit = _load_stl_like(p), None
    elif ext == ".3mf":
        mesh, unit = load_3mf(p)
    elif ext in (".step", ".stp"):
        mesh, unit = load_step(p, step_detail), "meter"
    else:
        raise GeometryImportError(f"Nicht unterstuetztes Format: {ext}")
    if mesh.n_cells == 0:
        raise GeometryImportError("Die Datei enthaelt keine Dreiecke.")
    return ImportResult(_finalize(mesh), unit, p)


def _finalize(mesh: pv.PolyData) -> pv.PolyData:
    return mesh.triangulate().clean()


def _triangles_to_polydata(tri: np.ndarray) -> pv.PolyData:
    """tri: (n, 3, 3) float -> PolyData mit zusammengefuehrten Punkten."""
    n = len(tri)
    points = tri.reshape(-1, 3).astype(np.float64)
    faces = np.hstack([np.full((n, 1), 3), np.arange(3 * n).reshape(n, 3)]).ravel()
    return pv.PolyData(points, faces).clean()


def _read_binary_stl(data: bytes) -> np.ndarray | None:
    if len(data) < 84:
        return None
    n = int(np.frombuffer(data, dtype="<u4", count=1, offset=80)[0])
    if 84 + 50 * n == len(data) and n > 0:
        dt = np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
        return np.frombuffer(data, dtype=dt, count=n, offset=84)["v"]
    return None


def _load_stl_like(path: Path) -> pv.PolyData:
    data = path.read_bytes()
    tri = _read_binary_stl(data)
    if tri is not None:
        return _triangles_to_polydata(tri)
    head = data[:512].lstrip().lower()
    if head.startswith(b"solid") and b"facet" in data[:2048].lower():
        return pv.read(str(path))
    # Rohe Dreiecksliste (9 float32 pro Dreieck)
    if len(data) % 36 == 0 and len(data) > 0:
        tri = np.frombuffer(data, dtype="<f4").reshape(-1, 3, 3)
        if np.isfinite(tri).all():
            return _triangles_to_polydata(tri)
    raise GeometryImportError("Datei ist weder Binary-/ASCII-STL noch eine rohe Dreiecksliste.")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_transform(text: str | None) -> np.ndarray:
    m = np.eye(4)
    if not text:
        return m
    v = [float(x) for x in text.split()]
    if len(v) != 12:
        return m
    # 3MF: Zeilenvektor-Konvention, x' = x*M (4x3-Matrix)
    m[:3, :3] = np.array(v[:9]).reshape(3, 3).T
    m[:3, 3] = v[9:12]
    return m


def load_3mf(path: Path) -> tuple[pv.PolyData, str]:
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise GeometryImportError("Keine gueltige 3MF-Datei (kein ZIP-Archiv).") from exc
    with zf:
        models = [n for n in zf.namelist() if n.lower().endswith(".model")]
        if not models:
            raise GeometryImportError("3MF enthaelt kein 3D-Modell.")
        root_name = next((n for n in models if n.lower() == "3d/3dmodel.model"), models[0])
        root = ET.fromstring(zf.read(root_name))

    unit = root.get("unit", "millimeter")
    objects: dict[str, ET.Element] = {}
    build: list[ET.Element] = []
    for el in root.iter():
        t = _local(el.tag)
        if t == "object":
            objects[el.get("id")] = el
        elif t == "item":
            build.append(el)

    cache: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {}

    def resolve(obj_id: str, depth: int = 0) -> list[tuple[np.ndarray, np.ndarray]]:
        if obj_id in cache:
            return cache[obj_id]
        if depth > 16 or obj_id not in objects:
            return []
        obj = objects[obj_id]
        parts: list[tuple[np.ndarray, np.ndarray]] = []
        for child in obj:
            ct = _local(child.tag)
            if ct == "mesh":
                verts: list[list[float]] = []
                tris: list[list[int]] = []
                for sub in child.iter():
                    st = _local(sub.tag)
                    if st == "vertex":
                        verts.append([float(sub.get("x")), float(sub.get("y")), float(sub.get("z"))])
                    elif st == "triangle":
                        tris.append([int(sub.get("v1")), int(sub.get("v2")), int(sub.get("v3"))])
                if verts and tris:
                    parts.append((np.array(verts), np.array(tris)))
            elif ct == "components":
                for comp in child:
                    m = _parse_transform(comp.get("transform"))
                    for v, t in resolve(comp.get("objectid"), depth + 1):
                        parts.append((v @ m[:3, :3].T + m[:3, 3], t))
        cache[obj_id] = parts
        return parts

    if not build:
        build = [ET.Element("item", {"objectid": oid}) for oid, o in objects.items()
                 if o.get("type", "model") == "model"]

    all_v: list[np.ndarray] = []
    all_t: list[np.ndarray] = []
    offset = 0
    for item in build:
        m = _parse_transform(item.get("transform"))
        for v, t in resolve(item.get("objectid")):
            all_v.append(v @ m[:3, :3].T + m[:3, 3])
            all_t.append(t + offset)
            offset += len(v)
    if not all_v:
        raise GeometryImportError("3MF enthaelt keine Dreiecksnetze.")

    verts_a = np.vstack(all_v)
    tris_a = np.vstack(all_t)
    faces = np.hstack([np.full((len(tris_a), 1), 3), tris_a]).ravel()
    return pv.PolyData(verts_a, faces), unit


def load_step(path: Path, detail: float = 1.0) -> pv.PolyData:
    """STEP -> Oberflaechennetz in Metern ueber Gmsh/OpenCASCADE. detail > 1 = feiner."""
    import gmsh

    tmp = Path(tempfile.mkdtemp()) / "step_surface.stl"
    gmsh.initialize(interruptible=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setString("Geometry.OCCTargetUnit", "M")
        gmsh.option.setNumber("Geometry.OCCSewFaces", 1)
        gmsh.option.setNumber("Geometry.OCCFixSmallEdges", 1)
        gmsh.option.setNumber("Geometry.OCCFixSmallFaces", 1)
        gmsh.model.add("step")
        gmsh.model.occ.importShapes(str(path))
        gmsh.model.occ.synchronize()
        xmin, ymin, zmin, xmax, ymax, zmax = gmsh.model.getBoundingBox(-1, -1)
        diag = float(np.linalg.norm([xmax - xmin, ymax - ymin, zmax - zmin]))
        if diag <= 0:
            raise GeometryImportError("STEP-Datei enthaelt keine Geometrie.")
        gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
        gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 16)
        gmsh.option.setNumber("Mesh.MeshSizeMax", diag / (60.0 * detail))
        gmsh.option.setNumber("Mesh.MeshSizeMin", diag / (2000.0 * detail))
        gmsh.option.setNumber("Mesh.Algorithm", 6)
        gmsh.option.setNumber("Mesh.Binary", 1)
        gmsh.model.mesh.generate(2)
        gmsh.write(str(tmp))
    except GeometryImportError:
        raise
    except Exception as exc:  # gmsh wirft generische Exceptions
        raise GeometryImportError(f"STEP-Import fehlgeschlagen: {exc}") from exc
    finally:
        gmsh.finalize()
    try:
        return _load_stl_like(tmp)
    finally:
        tmp.unlink(missing_ok=True)
        tmp.parent.rmdir()
