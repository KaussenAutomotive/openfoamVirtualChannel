"""Erzeugt einen OpenFOAM-Fall (Foundation dev, foamRun/incompressibleFluid) fuer einen Fahrzeug-Windkanal."""
from __future__ import annotations

import math
import shutil
from dataclasses import dataclass
from pathlib import Path

import pyvista as pv

from .foam_utils import VEHICLE_PATCH_REGEX, _hdr, _vec
from .geometry import VehicleInfo, export_stl
from .settings import G, TunnelSettings
from .vof_case import FREE_SURFACE_SAMPLE, air_height
from .vof_case import case_files as vof_files
from .vof_case import control_dict as vof_control_dict
from .vof_case import force_coeffs as vof_force_coeffs


@dataclass
class Domain:
    xmin: float
    xmax: float
    ymin: float
    ymax: float
    zmin: float
    zmax: float

    @property
    def volume(self) -> float:
        return (self.xmax - self.xmin) * (self.ymax - self.ymin) * (self.zmax - self.zmin)


def waterline_z(info: VehicleInfo, s: TunnelSettings) -> float:
    return info.bounds[4] + max(s.draft, 0.01)


def compute_domain(info: VehicleInfo, s: TunnelSettings) -> Domain:
    b = info.bounds
    yc = (b[2] + b[3]) / 2
    half = max(s.side * info.width, 1.5 * info.width)
    if s.is_vof:
        # Wellen: Luftraum ueber der Wasserlinie, breit genug fuer den Kelvin-Keil
        zmax = waterline_z(info, s) + air_height(info)
        half = max(half, 1.0 * info.length)
    elif s.is_water:
        # Doppelkoerper-Modell: Wasseroberflaeche = starrer Deckel auf Hoehe der Wasserlinie
        zmax = waterline_z(info, s)
    else:
        zmax = max(s.top * b[5], 2.0 * b[5])
    return Domain(
        xmin=b[0] - s.upstream * info.length,
        xmax=b[1] + s.downstream * info.length,
        ymin=yc - half,
        ymax=yc + half,
        zmin=0.0,
        zmax=zmax,
    )


def blockage_ratio(info: VehicleInfo, s: TunnelSettings) -> float:
    d = compute_domain(info, s)
    return info.frontal_area / ((d.ymax - d.ymin) * (d.zmax - d.zmin))


@dataclass
class Refinement:
    near_min: tuple[float, float, float]
    near_max: tuple[float, float, float]
    wake_min: tuple[float, float, float]
    wake_max: tuple[float, float, float]
    inside_point: tuple[float, float, float]
    cells: tuple[int, int, int]
    dx0: float
    slab_min: tuple[float, float, float] | None = None  # Verfeinerung an der freien Oberflaeche
    slab_max: tuple[float, float, float] | None = None


def compute_refinement(info: VehicleInfo, s: TunnelSettings) -> Refinement:
    b = info.bounds
    L, W = info.length, info.width
    d = compute_domain(info, s)
    q = s.preset
    yc = (b[2] + b[3]) / 2
    dx0 = L / q.base_div
    if s.is_water:  # flacher Kanal: Basiszellen nicht hoeher als 1/3 der Wassertiefe
        dx0 = min(dx0, (d.zmax - d.zmin) / 3)
    cells = (
        max(4, round((d.xmax - d.xmin) / dx0)),
        max(4, round((d.ymax - d.ymin) / dx0)),
        max(4, round((d.zmax - d.zmin) / dx0)),
    )
    pad = 0.1 * L
    near_min = (b[0] - pad, b[2] - pad, -pad)
    near_max = (b[1] + pad, b[3] + pad, b[5] + pad)
    wake_min = (b[1] - 0.2 * L, yc - 0.6 * W - pad, -pad)
    wake_max = (b[1] + 2.0 * L, yc + 0.6 * W + pad, 1.4 * b[5])
    inside = (b[0] - 0.5 * L, yc, 0.5 * min(b[5], d.zmax))
    slab_min = slab_max = None
    if s.is_vof:  # Verfeinerungsschicht um die Wasserlinie ueber den ganzen Kanal
        z_w = waterline_z(info, s)
        t = max(0.06 * L, 0.8 * s.speed_ms**2 / (2 * G))  # Bugwellenhoehe ~ U^2/2g
        slab_min = (d.xmin, d.ymin, z_w - t)
        slab_max = (d.xmax, d.ymax, z_w + t)
    return Refinement(near_min, near_max, wake_min, wake_max, inside, cells, dx0, slab_min, slab_max)


def estimate_cells(info: VehicleInfo, s: TunnelSettings) -> int:
    q = s.preset
    r = compute_refinement(info, s)

    d = compute_domain(info, s)
    lims = ((d.xmin, d.xmax), (d.ymin, d.ymax), (d.zmin, d.zmax))

    def vol(lo, hi):  # nur der Anteil innerhalb des Kanals zaehlt
        return math.prod(max(min(h, u) - max(l, a), 0.0) for l, h, (a, u) in zip(lo, hi, lims))

    base = math.prod(r.cells)
    near = vol(r.near_min, r.near_max) / (r.dx0 / 2**q.near_level) ** 3
    wake = vol(r.wake_min, r.wake_max) / (r.dx0 / 2**q.wake_level) ** 3
    surf = info.surface_area / (r.dx0 / 2**q.surf_max) ** 2 * 6
    slab = 0.0
    if r.slab_min is not None:
        slab = vol(r.slab_min, r.slab_max) / (r.dx0 / 2**q.surf_min) ** 3
    return int(base + near + wake * 0.6 + surf + slab)


@dataclass
class CaseInfo:
    path: Path
    run_script: Path
    patches_regex: str
    domain: Domain
    est_cells: int


def build_case(case_dir: Path, mesh: pv.PolyData, info: VehicleInfo, s: TunnelSettings) -> CaseInfo:
    if case_dir.exists():
        shutil.rmtree(case_dir)
    for sub in ("0", "constant/geometry", "system"):
        (case_dir / sub).mkdir(parents=True)
    export_stl(mesh, case_dir / "constant" / "geometry" / "vehicle.stl")

    dom = compute_domain(info, s)
    ref = compute_refinement(info, s)
    files = {
        "system/blockMeshDict": _block_mesh(dom, ref, s),
        "system/snappyHexMeshDict": _snappy(s, ref, info),
        "system/controlDict": _control_dict(s),
        "system/fvSchemes": _FV_SCHEMES,
        "system/fvSolution": _fv_solution(),
        "system/decomposeParDict": _decompose(s),
        "system/functions": _functions(s),
        "system/forceCoeffs": _force_coeffs(info, s),
        "system/cutPlane": _cut_plane(info, s),
        "constant/momentumTransport": _momentum_transport(),
        "constant/physicalProperties": _physical_properties(s),
        "0/U": _field_u(s),
        "0/p": _field_p(),
        "0/nut": _field_nut(),
        "0/nuTilda": _field_nutilda(s),
    }
    if s.is_vof:
        for rel in ("constant/momentumTransport", "constant/physicalProperties", "0/U", "0/p", "0/nut", "0/nuTilda"):
            del files[rel]
        files.update(vof_files(info, s))
    for i, text in enumerate(_streamline_functions(info, s), start=1):
        files[f"system/streamlines{i}"] = text
    for rel, text in files.items():
        (case_dir / rel).write_text(text, encoding="utf-8", newline="\n")

    script = case_dir / "run_wsl.sh"
    script.write_text(_run_script(s), encoding="utf-8", newline="\n")
    return CaseInfo(case_dir, script, VEHICLE_PATCH_REGEX, dom, estimate_cells(info, s))


def _block_mesh(d: Domain, r: Refinement, s: TunnelSettings) -> str:
    x0, x1, y0, y1, z0, z1 = d.xmin, d.xmax, d.ymin, d.ymax, d.zmin, d.zmax
    top_name, top_type = ("atmosphere", "patch") if s.is_vof else ("upperWall", "symmetryPlane")
    floor_type = "symmetryPlane" if s.is_vof else "wall"
    verts = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
             (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    vtxt = "\n".join(f"    {_vec(v)}" for v in verts)
    nx, ny, nz = r.cells
    return _hdr("dictionary", "blockMeshDict", "system") + f"""vertices
(
{vtxt}
);

blocks
(
    hex (0 1 2 3 4 5 6 7) ({nx} {ny} {nz}) simpleGrading (1 1 1)
);

boundary
(
    front
    {{
        type symmetryPlane;
        faces ((3 7 6 2));
    }}
    back
    {{
        type symmetryPlane;
        faces ((1 5 4 0));
    }}
    inlet
    {{
        type patch;
        faces ((0 4 7 3));
    }}
    outlet
    {{
        type patch;
        faces ((2 6 5 1));
    }}
    lowerWall
    {{
        type {floor_type};
        faces ((0 3 2 1));
    }}
    {top_name}
    {{
        type {top_type};
        faces ((4 5 6 7));
    }}
);

// ************************************************************************* //
"""


def _snappy(s: TunnelSettings, r: Refinement, info: VehicleInfo) -> str:
    q = s.preset
    local = max(100_000, q.max_cells // max(s.cores, 1))
    slab_geom = slab_region = ""
    if r.slab_min is not None:
        slab_geom = f"""    waterSlab
    {{
        type box;
        min {_vec(r.slab_min)};
        max {_vec(r.slab_max)};
    }}
"""
        slab_region = f"""        waterSlab
        {{
            mode    inside;
            level   {q.surf_min};
        }}
"""
    return _hdr("dictionary", "snappyHexMeshDict", "system") + f"""castellatedMesh true;
snap            true;
addLayers       false;

geometry
{{
    vehicle
    {{
        type triSurface;
        file "vehicle.stl";
    }}
    nearBox
    {{
        type box;
        min {_vec(r.near_min)};
        max {_vec(r.near_max)};
    }}
    wakeBox
    {{
        type box;
        min {_vec(r.wake_min)};
        max {_vec(r.wake_max)};
    }}
{slab_geom}}};

castellatedMeshControls
{{
    maxLocalCells {local};
    maxGlobalCells {q.max_cells};
    minRefinementCells 10;
    maxLoadUnbalance 0.10;
    nCellsBetweenLevels 3;

    features ();

    refinementSurfaces
    {{
        vehicle
        {{
            level ({q.surf_min} {q.surf_max});
            patchInfo
            {{
                type wall;
                inGroups (vehicleGroup);
            }}
        }}
    }}

    resolveFeatureAngle 30;

    refinementRegions
    {{
        nearBox
        {{
            mode    inside;
            level   {q.near_level};
        }}
        wakeBox
        {{
            mode    inside;
            level   {q.wake_level};
        }}
{slab_region}    }}

    insidePoint {_vec(r.inside_point)};
    allowFreeStandingZoneFaces false;
}}

snapControls
{{
    nSmoothPatch 3;
    tolerance 4.0;
    nSolveIter 30;
    nRelaxIter 5;
}}

addLayersControls
{{
    relativeSizes true;
    layers {{}}
    expansionRatio 1.0;
    finalLayerThickness 0.3;
    minThickness 0.1;
}}

meshQualityControls
{{
    maxNonOrtho 65;
    maxBoundarySkewness 20;
    maxInternalSkewness 4;
    maxConcave 80;
    minFlatness 0.5;
    minVol -1e30;
    minTetQuality 1e-30;
    minTwist 0.02;
    minDeterminant 0.001;
    minFaceWeight 0.02;
    minVolRatio 0.01;
    nSmoothScale 4;
    errorReduction 0.75;
}}

mergeTolerance 1e-6;

// ************************************************************************* //
"""


def _control_dict(s: TunnelSettings) -> str:
    if s.is_vof:
        return vof_control_dict(s)
    n = s.n_iterations
    interval = max(50, n // 4)
    return _hdr("dictionary", "controlDict", "system") + f"""solver          incompressibleFluid;

startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         {n};
deltaT          1;

writeControl    timeStep;
writeInterval   {interval};
purgeWrite      2;
writeFormat     binary;
writePrecision  6;
writeCompression off;
timeFormat      general;
timePrecision   6;

runTimeModifiable true;

// ************************************************************************* //
"""


_FV_SCHEMES = _hdr("dictionary", "fvSchemes", "system") + """ddtSchemes
{
    default         steadyState;
}

gradSchemes
{
    default         Gauss linear;
    grad(U)         cellLimited Gauss linear 1;
    grad(nuTilda)   cellLimited Gauss linear 1;
}

divSchemes
{
    default         none;
    div(phi,U)      bounded Gauss linearUpwindV grad(U);
    div(phi,nuTilda) bounded Gauss upwind;
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}

laplacianSchemes
{
    default         Gauss linear corrected;
}

interpolationSchemes
{
    default         linear;
}

snGradSchemes
{
    default         corrected;
}

wallDist
{
    method meshWave;
}

// ************************************************************************* //
"""


def _fv_solution() -> str:
    return _hdr("dictionary", "fvSolution", "system") + """solvers
{
    p
    {
        solver          GAMG;
        tolerance       1e-07;
        relTol          0.1;
        smoother        GaussSeidel;
    }

    pFinal
    {
        $p;
        tolerance       1e-6;
        relTol          0;
    }

    Phi
    {
        $p;
    }

    "(U|nuTilda)"
    {
        solver          smoothSolver;
        smoother        GaussSeidel;
        tolerance       1e-08;
        relTol          0.1;
    }
}

SIMPLE
{
    nNonOrthogonalCorrectors 0;
    residualControl
    {
        p               1e-4;
        U               1e-4;
        nuTilda         1e-4;
    }
}

potentialFlow
{
    nNonOrthogonalCorrectors 10;
}

relaxationFactors
{
    p               0.3;
    U               0.5;
    nuTilda         0.5;
}

cache
{
    grad(U);
}

// ************************************************************************* //
"""


def _decompose(s: TunnelSettings) -> str:
    return _hdr("dictionary", "decomposeParDict", "system") + f"""numberOfSubdomains  {s.cores};
decomposer      scotch;
distributor     ptscotch;

// ************************************************************************* //
"""


def _functions(s: TunnelSettings) -> str:
    inc = "\n".join(f"#includeFunc streamlines{i}" for i in range(1, s.streamline_seeds + 1))
    return _hdr("dictionary", "functions", "system") + f"""#includeFunc forceCoeffs
#includeFunc cutPlane
{inc}

// ************************************************************************* //
"""


def _force_coeffs(info: VehicleInfo, s: TunnelSettings) -> str:
    if s.is_vof:
        return vof_force_coeffs(info, s)
    b = info.bounds
    cofr = ((b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2 if s.is_water else 0.0)
    return f"""type            forceCoeffs;
libs            ("libforces.so");

writeControl    timeStep;
writeInterval   1;

log             yes;

patches         ({VEHICLE_PATCH_REGEX});

rho             rhoInf;
rhoInf          1;

liftDir         (0 0 1);
dragDir         (1 0 0);
CofR            {_vec(cofr)};
pitchAxis       (0 1 0);
magUInf         {s.speed_ms:.6g};
lRef            {info.length:.6g};
Aref            {info.frontal_area:.6g};
"""


def _cut_plane(info: VehicleInfo, s: TunnelSettings) -> str:
    b = info.bounds
    yc = (b[2] + b[3]) / 2
    z_cut = 0.5 * (b[4] + min(b[5], waterline_z(info, s))) if s.is_water else 0.4 * b[5]
    fields = "(p_rgh U)" if s.is_vof else "(p U)"
    free_surface = FREE_SURFACE_SAMPLE if s.is_vof else ""
    return f"""type            surfaces;
libs            ("libsampling.so");

writeControl    writeTime;

surfaceFormat   vtk;
writeFormat     binary;

fields          {fields};

interpolationScheme cellPoint;

surfaces
{{
    yNormal
    {{
        type            cutPlane;
        planeType       pointAndNormal;
        point           (0 {yc:.6g} 0);
        normal          (0 1 0);
        interpolate     true;
    }}
    zNormal
    {{
        type            cutPlane;
        planeType       pointAndNormal;
        point           (0 0 {z_cut:.6g});
        normal          (0 0 1);
        interpolate     true;
    }}
{free_surface}}}
"""


def _streamline_functions(info: VehicleInfo, s: TunnelSettings) -> list[str]:
    b = info.bounds
    yc = (b[2] + b[3]) / 2
    n = s.streamline_seeds
    x = b[0] - 0.3 * info.length
    if s.is_water:
        zmax = waterline_z(info, s)
        z_lo = max(0.04 * zmax, b[4] - 0.4 * info.height)
        z_hi = min(b[5] + 0.4 * info.height, 0.97 * zmax)
    else:
        z_lo, z_hi = 0.04 * b[5], 1.4 * b[5]
    fields = "(U)" if s.is_vof else "(p U)"
    out = []
    for i in range(n):
        frac = 0.0 if n == 1 else (i / (n - 1) - 0.5) * 2  # -1 .. 1
        y = yc + frac * 0.8 * info.width + 1e-7
        out.append(f"""type            streamlines;
libs            ("libfieldFunctionObjects.so");

writeControl    writeTime;

setFormat       vtk;
direction       forward;
fields          {fields};
lifeTime        10000;
nSubCycle       5;
cloudName       particleTracks{i + 1};

seedSampleSet
{{
    type        lineUniform;
    start       ({x:.6g} {y:.6g} {z_lo:.6g});
    end         ({x:.6g} {y:.6g} {z_hi:.6g});
    nPoints     20;
}}
""")
    return out


def _momentum_transport() -> str:
    return _hdr("dictionary", "momentumTransport", "constant") + """simulationType RAS;

RAS
{
    model           SpalartAllmaras;
    turbulence      on;
}

// ************************************************************************* //
"""


def _physical_properties(s: TunnelSettings) -> str:
    return _hdr("dictionary", "physicalProperties", "constant") + f"""viscosityModel  constant;

nu              [0 2 -1 0 0 0 0] {s.nu:.6g};

// ************************************************************************* //
"""


def _field_u(s: TunnelSettings) -> str:
    u = s.speed_ms
    lower = (f"type fixedValue; value uniform ({u:.6g} 0 0);" if s.rolling_road else "type noSlip;")
    return _hdr("volVectorField", "U", "0") + f"""dimensions      [0 1 -1 0 0 0 0];

internalField   uniform ({u:.6g} 0 0);

boundaryField
{{
    inlet
    {{
        type            fixedValue;
        value           uniform ({u:.6g} 0 0);
    }}
    outlet
    {{
        type            inletOutlet;
        inletValue      uniform (0 0 0);
        value           uniform ({u:.6g} 0 0);
    }}
    lowerWall
    {{
        {lower}
    }}
    {VEHICLE_PATCH_REGEX}
    {{
        type            noSlip;
    }}
    "(upperWall|front|back)"
    {{
        type            symmetryPlane;
    }}
    "proc.*"
    {{
        type            processor;
    }}
}}

// ************************************************************************* //
"""


def _field_p() -> str:
    return _hdr("volScalarField", "p", "0") + f"""dimensions      [0 2 -2 0 0 0 0];

internalField   uniform 0;

boundaryField
{{
    inlet
    {{
        type            zeroGradient;
    }}
    outlet
    {{
        type            fixedValue;
        value           uniform 0;
    }}
    "(lowerWall|vehicle.*)"
    {{
        type            zeroGradient;
    }}
    "(upperWall|front|back)"
    {{
        type            symmetryPlane;
    }}
    "proc.*"
    {{
        type            processor;
    }}
}}

// ************************************************************************* //
"""


def _field_nut() -> str:
    return _hdr("volScalarField", "nut", "0") + f"""dimensions      [0 2 -1 0 0 0 0];

internalField   uniform 0;

boundaryField
{{
    "(inlet|outlet)"
    {{
        type            calculated;
        value           uniform 0;
    }}
    "(lowerWall|vehicle.*)"
    {{
        type            nutUSpaldingWallFunction;
        value           uniform 0;
    }}
    "(upperWall|front|back)"
    {{
        type            symmetryPlane;
    }}
    "proc.*"
    {{
        type            processor;
    }}
}}

// ************************************************************************* //
"""


def _field_nutilda(s: TunnelSettings) -> str:
    nt = 4.0 * s.nu
    return _hdr("volScalarField", "nuTilda", "0") + f"""dimensions      [0 2 -1 0 0 0 0];

internalField   uniform {nt:.6g};

boundaryField
{{
    inlet
    {{
        type            fixedValue;
        value           uniform {nt:.6g};
    }}
    outlet
    {{
        type            inletOutlet;
        inletValue      uniform {nt:.6g};
        value           uniform {nt:.6g};
    }}
    "(lowerWall|vehicle.*)"
    {{
        type            fixedValue;
        value           uniform 0;
    }}
    "(upperWall|front|back)"
    {{
        type            symmetryPlane;
    }}
    "proc.*"
    {{
        type            processor;
    }}
}}

// ************************************************************************* //
"""


def _run_script(s: TunnelSettings) -> str:
    w = s.wsl
    if s.is_vof:
        steps = """run blockMesh blockMesh
run decomposePar decomposePar -noFields
run snappyHexMesh mpirun -np $NP snappyHexMesh -overwrite -parallel
run_soft checkMesh mpirun -np $NP checkMesh -parallel -latestTime
run renumberMesh mpirun -np $NP renumberMesh -overwrite -noFields -parallel
run decomposeFields decomposePar -fields -copyZero
run foamRun mpirun -np $NP foamRun -parallel
run_soft foamToVTK mpirun -np $NP foamToVTK -parallel -latestTime -noInternal -fields '(p_rgh U)' -excludePatches '(inlet outlet lowerWall atmosphere front back)'"""
    else:
        steps = """run blockMesh blockMesh
run decomposePar decomposePar -copyZero
run snappyHexMesh mpirun -np $NP snappyHexMesh -overwrite -parallel
run_soft checkMesh mpirun -np $NP checkMesh -parallel -latestTime
run renumberMesh mpirun -np $NP renumberMesh -overwrite -parallel
run_soft potentialFoam mpirun -np $NP potentialFoam -initialiseUBCs -parallel
run foamRun mpirun -np $NP foamRun -parallel
run_soft foamToVTK mpirun -np $NP foamToVTK -parallel -latestTime -noInternal -fields '(p U)' -excludePatches '(inlet outlet lowerWall upperWall front back)'"""
    return f"""#!/bin/bash
# Von windtunnel erzeugt. Kopiert den Fall ins WSL-Dateisystem, rechnet dort und kopiert Ergebnisse zurueck.
SRC="$(cd "$(dirname "$0")" && pwd)"
source "{w.bashrc}" || {{ echo "##FAIL OpenFOAM-Umgebung nicht gefunden"; exit 1; }}
NAME="{s.case_name}"
WORK="{w.work_dir}/$NAME"
NP={s.cores}
echo $$ > "/tmp/windtunnel_$NAME.pid"

run() {{
    local name="$1"; shift
    echo "##STEP $name"
    "$@" 2>&1 | tee "log.$name"
    local rc=${{PIPESTATUS[0]}}
    if [ $rc -ne 0 ]; then echo "##FAIL $name (Exit $rc)"; exit $rc; fi
}}
run_soft() {{
    local name="$1"; shift
    echo "##STEP $name"
    "$@" 2>&1 | tee "log.$name"
    return 0
}}

echo "##STEP Fall kopieren"
rm -rf "$WORK" && mkdir -p "$WORK" || {{ echo "##FAIL Arbeitsverzeichnis"; exit 1; }}
cp -r "$SRC"/. "$WORK"/ || {{ echo "##FAIL Kopieren"; exit 1; }}
rm -rf "$WORK/results"
cd "$WORK" || exit 1

{steps}

echo "##STEP Ergebnisse zurueckkopieren"
rm -rf "$SRC/results" && mkdir -p "$SRC/results"
cp -rL postProcessing VTK "$SRC/results/" 2>/dev/null
cp log.* "$SRC/results/" 2>/dev/null
echo "##DONE"
"""
