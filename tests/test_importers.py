import zipfile
from pathlib import Path

import numpy as np
import pyvista as pv
import pytest

from windtunnel.importers import GeometryImportError, load_geometry


def _box() -> pv.PolyData:
    return pv.Box(bounds=(0, 4, 0, 2, 0, 1.5)).triangulate()


def _write_binary_stl(mesh: pv.PolyData, path: Path) -> None:
    mesh.save(str(path), binary=True)


def test_binary_stl(tmp_path):
    p = tmp_path / "a.stl"
    _write_binary_stl(_box(), p)
    r = load_geometry(p)
    assert r.native_unit is None
    assert r.mesh.n_cells == 12
    assert r.mesh.bounds == pytest.approx((0, 4, 0, 2, 0, 1.5))


def test_ascii_stl(tmp_path):
    p = tmp_path / "a.stl"
    _box().save(str(p), binary=False)
    assert load_geometry(p).mesh.n_cells == 12


def test_bin_is_binary_stl(tmp_path):
    p = tmp_path / "a.bin"
    _write_binary_stl(_box().copy(), tmp_path / "tmp.stl")
    (tmp_path / "tmp.stl").rename(p)
    assert load_geometry(p).mesh.n_cells == 12


def test_bin_raw_triangles(tmp_path):
    tri = np.array([[[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 0, 0], [0, 1, 0], [0, 0, 1]]], dtype="<f4")
    p = tmp_path / "raw.bin"
    p.write_bytes(tri.tobytes())
    assert load_geometry(p).mesh.n_cells == 2


def test_garbage_rejected(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"abc")
    with pytest.raises(GeometryImportError):
        load_geometry(p)


_MODEL = """<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
 <resources>
  <object id="1" type="model"><mesh>
   <vertices><vertex x="0" y="0" z="0"/><vertex x="1000" y="0" z="0"/><vertex x="0" y="1000" z="0"/><vertex x="0" y="0" z="1000"/></vertices>
   <triangles><triangle v1="0" v2="2" v3="1"/><triangle v1="0" v2="1" v3="3"/><triangle v1="1" v2="2" v3="3"/><triangle v1="2" v2="0" v3="3"/></triangles>
  </mesh></object>
  <object id="2" type="model"><components><component objectid="1" transform="1 0 0 0 1 0 0 0 1 5000 0 0"/></components></object>
 </resources>
 <build><item objectid="2" transform="1 0 0 0 1 0 0 0 1 0 0 100"/></build>
</model>"""


def test_3mf_components_transform_unit(tmp_path):
    p = tmp_path / "a.3mf"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("3D/3dmodel.model", _MODEL)
    r = load_geometry(p)
    assert r.native_unit == "millimeter"
    assert r.mesh.n_cells == 4
    assert r.mesh.bounds == pytest.approx((5000, 6000, 0, 1000, 100, 1100))


def test_3mf_not_a_zip(tmp_path):
    p = tmp_path / "a.3mf"
    p.write_text("nope")
    with pytest.raises(GeometryImportError):
        load_geometry(p)


def test_step_box_in_meters(tmp_path):
    import gmsh

    p = tmp_path / "box.step"
    gmsh.initialize(interruptible=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("b")
        gmsh.model.occ.addBox(0, 0, 0, 4000, 1800, 1500)  # mm im STEP
        gmsh.model.occ.synchronize()
        gmsh.write(str(p))
    finally:
        gmsh.finalize()
    r = load_geometry(p)
    assert r.native_unit == "meter"
    b = r.mesh.bounds
    assert (b[1] - b[0]) == pytest.approx(4.0, rel=0.02)
    assert r.mesh.n_cells > 0
