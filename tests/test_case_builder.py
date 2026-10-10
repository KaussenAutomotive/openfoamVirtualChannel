import pyvista as pv

from windtunnel.case_builder import blockage_ratio, build_case, compute_domain, estimate_cells
from windtunnel.geometry import Placement, analyze, place_vehicle
from windtunnel.settings import TunnelSettings


def _vehicle():
    raw = pv.ParametricSuperEllipsoid(xradius=2.2, yradius=0.9, zradius=0.6, n1=0.5, n2=0.5).triangulate().clean()
    mesh = place_vehicle(raw, Placement(rot_z=0))
    return mesh, analyze(mesh)


def test_place_vehicle_on_ground():
    mesh, info = _vehicle()
    assert mesh.bounds[4] == 0.0
    assert abs(mesh.bounds[2] + mesh.bounds[3]) < 1e-9
    assert info.watertight
    assert 0.6 * info.width * info.height < info.frontal_area < info.width * info.height


def test_domain_and_estimates():
    _, info = _vehicle()
    s = TunnelSettings()
    d = compute_domain(info, s)
    assert d.xmin < info.bounds[0] and d.xmax > info.bounds[1]
    assert 0 < blockage_ratio(info=info, s=s) < 0.1
    s_fine = TunnelSettings(quality="Fein")
    assert estimate_cells(info, s_fine) > estimate_cells(info, s)


def test_build_case_files(tmp_path):
    mesh, info = _vehicle()
    s = TunnelSettings(cores=4, streamline_seeds=3, rolling_road=False)
    ci = build_case(tmp_path / "case", mesh, info, s)
    c = ci.path
    for rel in ("constant/geometry/vehicle.stl", "system/blockMeshDict", "system/snappyHexMeshDict",
                "system/controlDict", "system/fvSolution", "system/forceCoeffs", "system/streamlines3",
                "0/U", "0/p", "0/nut", "0/nuTilda", "run_wsl.sh"):
        assert (c / rel).is_file(), rel
    assert not (c / "system/streamlines4").exists()
    assert "type noSlip;" in (c / "0/U").read_text()
    assert "numberOfSubdomains  4;" in (c / "system/decomposeParDict").read_text()
    assert b"\r\n" not in (c / "run_wsl.sh").read_bytes()
    assert "#includeFunc streamlines3" in (c / "system/functions").read_text()
