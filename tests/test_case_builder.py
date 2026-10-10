import pytest
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


def test_waterline_wet_frontal_area():
    mesh, _ = _vehicle()
    full = analyze(mesh).frontal_area
    half = analyze(mesh, mesh.bounds[4] + 0.6)
    assert 0.2 * full < half.frontal_area < 0.8 * full
    assert half.immersion == pytest.approx(0.5, abs=0.01)
    assert analyze(mesh, mesh.bounds[5] + 3.0).immersion == 1.0


def test_water_domain_follows_waterline(tmp_path):
    mesh, _ = _vehicle()
    keel = mesh.bounds[4]
    for draft in (0.5, 6.0):  # teilweise eingetaucht, tief getaucht (U-Boot)
        s = TunnelSettings(medium="Wasser", draft=draft, rolling_road=False, cores=4)
        info = analyze(mesh, keel + draft)
        assert compute_domain(info, s).zmax == pytest.approx(keel + draft)
        ci = build_case(tmp_path / f"w{draft}", mesh, info, s)
        assert ci.domain.zmax == pytest.approx(keel + draft)
        assert "1e-06" in (ci.path / "constant/physicalProperties").read_text()
        _, _, z = (float(v) for v in __import__("re").search(r"insidePoint \(([^)]*)\)", (ci.path / "system/snappyHexMeshDict").read_text()).group(1).split())
        assert 0 < z < keel + draft


def test_vof_case_files(tmp_path):
    from windtunnel.case_builder import waterline_z
    from windtunnel.vof_case import air_height, froude_numbers

    mesh, _ = _vehicle()
    keel = mesh.bounds[4]
    s = TunnelSettings(medium="Wasser", free_surface=True, draft=0.5, speed_kmh=9.5, cores=4)
    info = analyze(mesh, keel + 0.5)
    assert s.is_vof and not TunnelSettings(medium="Luft", free_surface=True).is_vof
    d = compute_domain(info, s)
    assert d.zmax == pytest.approx(waterline_z(info, s) + air_height(info))
    assert d.ymax - d.ymin >= 2.0 * info.length  # Platz fuer den Kelvin-Keil
    ci = build_case(tmp_path / "vof", mesh, info, s)
    c = ci.path
    for rel in ("0/alpha.water", "0/p_rgh", "0/U", "0/k", "0/omega", "0/nut", "constant/g", "constant/hRef",
                "constant/phaseProperties", "constant/physicalProperties.water",
                "constant/physicalProperties.air", "constant/momentumTransport"):
        assert (c / rel).is_file(), rel
    assert not (c / "0/p").exists() and not (c / "0/nuTilda").exists()
    assert "solver          incompressibleVoF;" in (c / "system/controlDict").read_text()
    assert "localEuler" in (c / "system/fvSchemes").read_text()
    assert "atmosphere" in (c / "system/blockMeshDict").read_text()
    assert "waterSlab" in (c / "system/snappyHexMeshDict").read_text()
    assert "freeSurface" in (c / "system/cutPlane").read_text()
    assert f"value           {keel + 0.5:.6g};" in (c / "constant/hRef").read_text()
    script = (c / "run_wsl.sh").read_text()
    assert "decomposeFields" in script and "potentialFoam" not in script
    fr, fr_h, lam = froude_numbers(info, s)
    assert 0.1 < fr < 1 and lam == pytest.approx(2 * 3.14159265 * (9.5 / 3.6) ** 2 / 9.81, rel=1e-3)
