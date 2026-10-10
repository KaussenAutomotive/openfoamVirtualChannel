"""OpenFOAM-Dateien fuer die Wasserkanal-Variante mit freier Oberflaeche (VoF, Wellenbildung)."""
from __future__ import annotations

import math

from .foam_utils import VEHICLE_PATCH_REGEX, _hdr, _vec
from .geometry import VehicleInfo
from .settings import G, NU_AIR, RHO_AIR, RHO_WATER, TunnelSettings

WAVE_PATCHES = "(inlet outlet lowerWall atmosphere front back)"


def waterline(info: VehicleInfo, s: TunnelSettings) -> float:
    return info.bounds[4] + max(s.draft, 0.01)


def air_height(info: VehicleInfo) -> float:
    return max(1.5 * info.height, 0.15 * info.length)


def froude_numbers(info: VehicleInfo, s: TunnelSettings) -> tuple[float, float, float]:
    """(Froude-Zahl bezogen auf die Laenge, Tiefen-Froude-Zahl, Wellenlaenge in m)."""
    u = s.speed_ms
    depth = waterline(info, s)
    return u / math.sqrt(G * info.length), u / math.sqrt(G * depth), 2 * math.pi * u * u / G


def control_dict(s: TunnelSettings) -> str:
    n = s.n_iterations
    return _hdr("dictionary", "controlDict", "system") + f"""solver          incompressibleVoF;

startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         {n};
deltaT          1;

writeControl    timeStep;
writeInterval   {max(50, n // 4)};
purgeWrite      2;
writeFormat     binary;
writePrecision  6;
writeCompression off;
timeFormat      general;
timePrecision   6;

runTimeModifiable yes;

// ************************************************************************* //
"""


FV_SCHEMES = _hdr("dictionary", "fvSchemes", "system") + """ddtSchemes
{
    default         localEuler;
}

gradSchemes
{
    default         Gauss linear;
    limitedGrad     cellLimited Gauss linear 1;
}

divSchemes
{
    div(rhoPhi,U)   Gauss linearUpwind grad(U);
    div(phi,alpha)  Gauss interfaceCompression vanLeer 1;
    div(phi,k)      Gauss linearUpwind limitedGrad;
    div(phi,omega)  Gauss linearUpwind limitedGrad;
    div(((rho*nuEff)*dev2(T(grad(U))))) Gauss linear;
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

FV_SOLUTION = _hdr("dictionary", "fvSolution", "system") + """solvers
{
    "alpha.water.*"
    {
        nCorrectors     2;
        nSubCycles      1;
        MULESCorr       yes;
        alphaApplyPrevCorr  yes;

        MULES
        {
            nIter            1;
        }

        solver          smoothSolver;
        smoother        symGaussSeidel;
        tolerance       1e-8;
        relTol          0;
        minIter         1;
    }

    "pcorr.*"
    {
        solver          PCG;
        preconditioner
        {
            preconditioner  GAMG;
            smoother        GaussSeidel;
            tolerance       1e-5;
            relTol          0;
        }
        tolerance       1e-5;
        relTol          0;
    }

    p_rgh
    {
        solver          GAMG;
        smoother        DIC;
        tolerance       1e-8;
        relTol          0.01;
    }

    p_rghFinal
    {
        $p_rgh;
        relTol          0;
    }

    "(U|k|omega).*"
    {
        solver          smoothSolver;
        smoother        symGaussSeidel;
        nSweeps         1;
        tolerance       1e-7;
        relTol          0.1;
        minIter         1;
    }
}

PIMPLE
{
    momentumPredictor   no;
    nOuterCorrectors    1;
    nCorrectors         2;
    nNonOrthogonalCorrectors 0;

    maxCo               10;
    maxAlphaCo          1;
    rDeltaTSmoothingCoeff 0.025;
    rDeltaTDampingCoeff 0.5;
    nAlphaSpreadIter    0;
    nAlphaSweepIter     0;
    maxDeltaT           1;
}

relaxationFactors
{
    equations
    {
        ".*" 1;
    }
}

cache
{
    grad(U);
}

// ************************************************************************* //
"""


def force_coeffs(info: VehicleInfo, s: TunnelSettings) -> str:
    b = info.bounds
    cofr = ((b[0] + b[1]) / 2, (b[2] + b[3]) / 2, (b[4] + b[5]) / 2)
    return f"""type            forceCoeffs;
libs            ("libforces.so");

writeControl    timeStep;
writeInterval   1;

log             yes;

patches         ({VEHICLE_PATCH_REGEX});

rho             rho;
rhoInf          {RHO_WATER};

liftDir         (0 0 1);
dragDir         (1 0 0);
CofR            {_vec(cofr)};
pitchAxis       (0 1 0);
magUInf         {s.speed_ms:.6g};
lRef            {info.length:.6g};
Aref            {info.frontal_area:.6g};
"""


FREE_SURFACE_SAMPLE = """    freeSurface
    {
        type            isoSurface;
        isoField        alpha.water;
        isoValue        0.5;
        interpolate     true;
    }
"""


def case_files(info: VehicleInfo, s: TunnelSettings) -> dict[str, str]:
    z_w = waterline(info, s)
    u = s.speed_ms
    k0 = 1.5 * (0.005 * u) ** 2
    nut0 = 5.0 * s.nu
    om0 = k0 / nut0
    wall = VEHICLE_PATCH_REGEX
    include = '#includeEtc "caseDicts/setConstraintTypes"'

    zonal = f"""{{
    type        zonal;
    defaultValue    0;
    zones
    {{
        water
        {{
            type            box;
            box             (-1e5 -1e5 -1e5) (1e5 1e5 {z_w:.6g});
            value           1;
        }}
    }}
}}"""

    alpha = _hdr("volScalarField", "alpha.water", "0") + f"""dimensions      [];

internalField
{zonal}

boundaryField
{{
    {include}

    inlet
    {{
        type            functionalFixedValue;
        function
        {{
            $internalField;
            zones
            {{
                water
                {{
                    zone
                    {{
                        type        patch;
                        patch       inlet;
                    }}
                }}
            }}
        }}
    }}
    outlet
    {{
        type            variableHeightFlowRate;
        lowerBound      0;
        upperBound      1;
    }}
    atmosphere
    {{
        type            inletOutlet;
        inletValue      uniform 0;
    }}
    {wall}
    {{
        type            zeroGradient;
    }}
}}

// ************************************************************************* //
"""

    p_rgh = _hdr("volScalarField", "p_rgh", "0") + f"""dimensions      [1 -1 -2 0 0 0 0];

internalField   uniform 0;

boundaryField
{{
    {include}

    inlet
    {{
        type            fixedFluxPressure;
        value           $internalField;
    }}
    outlet
    {{
        type            zeroGradient;
    }}
    atmosphere
    {{
        type            prghTotalPressure;
        p0              uniform 0;
    }}
    {wall}
    {{
        type            fixedFluxPressure;
        value           $internalField;
    }}
}}

// ************************************************************************* //
"""

    u_field = _hdr("volVectorField", "U", "0") + f"""UMean           {u:.6g};

dimensions      [0 1 -1 0 0 0 0];

internalField   uniform ($UMean 0 0);

boundaryField
{{
    {include}

    inlet
    {{
        type            fixedValue;
        value           $internalField;
    }}
    outlet
    {{
        type            outletPhaseMeanVelocity;
        alpha           alpha.water;
        UnMean          $UMean;
        value           $internalField;
    }}
    atmosphere
    {{
        type            pressureInletOutletVelocity;
        tangentialVelocity $internalField;
        value           uniform (0 0 0);
    }}
    {wall}
    {{
        type            noSlip;
    }}
}}

// ************************************************************************* //
"""

    def scalar(name: str, dims: str, value: float, wall_type: str, extra: str = "") -> str:
        return _hdr("volScalarField", name, "0") + f"""dimensions      {dims};

internalField   uniform {value:.6g};

boundaryField
{{
    {include}

    inlet
    {{
        type            fixedValue;
        value           $internalField;
    }}
    outlet
    {{
        type            inletOutlet;
        inletValue      $internalField;
    }}
    atmosphere
    {{
        type            inletOutlet;
        inletValue      $internalField;
    }}
    {wall}
    {{
        type            {wall_type};
        value           $internalField;{extra}
    }}
}}

// ************************************************************************* //
"""

    nut = _hdr("volScalarField", "nut", "0") + f"""dimensions      [0 2 -1 0 0 0 0];

internalField   uniform {nut0:.6g};

boundaryField
{{
    {include}

    inlet
    {{
        type            fixedValue;
        value           $internalField;
    }}
    outlet
    {{
        type            zeroGradient;
    }}
    atmosphere
    {{
        type            zeroGradient;
    }}
    {wall}
    {{
        type            nutkWallFunction;
        value           $internalField;
    }}
}}

// ************************************************************************* //
"""

    def constant(cls: str, obj: str, body: str) -> str:
        return _hdr(cls, obj, "constant") + body + "\n// ************************************************************************* //\n"

    return {
        "0/alpha.water": alpha,
        "0/p_rgh": p_rgh,
        "0/U": u_field,
        "0/k": scalar("k", "[0 2 -2 0 0 0 0]", k0, "kqRWallFunction"),
        "0/omega": scalar("omega", "[0 0 -1 0 0 0 0]", om0, "omegaWallFunction"),
        "0/nut": nut,
        "constant/g": constant("uniformDimensionedVectorField", "g",
                               f"dimensions      [0 1 -2 0 0 0 0];\nvalue           (0 0 -{G});\n"),
        "constant/hRef": constant("uniformDimensionedScalarField", "hRef",
                                  f"dimensions      [0 1 0 0 0 0 0];\nvalue           {z_w:.6g};\n"),
        "constant/phaseProperties": constant("dictionary", "phaseProperties",
                                             "phases          (water air);\n\nsigma           0;\n"),
        "constant/physicalProperties.water": constant(
            "dictionary", "physicalProperties.water",
            f"viscosityModel  constant;\n\nnu              {s.nu:.6g};\nrho             {RHO_WATER};\n"),
        "constant/physicalProperties.air": constant(
            "dictionary", "physicalProperties.air",
            f"viscosityModel  constant;\n\nnu              {NU_AIR};\nrho             {RHO_AIR};\n"),
        "constant/momentumTransport": constant(
            "dictionary", "momentumTransport",
            "simulationType  RAS;\n\nRAS\n{\n    model           kOmegaSST;\n    turbulence      on;\n}\n"),
        "system/fvSchemes": FV_SCHEMES,
        "system/fvSolution": FV_SOLUTION,
    }
