"""Gemeinsame Hilfen zum Schreiben von OpenFOAM-Dictionaries."""
from __future__ import annotations


VEHICLE_PATCH_REGEX = '"vehicle.*"'

_HEADER = """/*--------------------------------*- C++ -*----------------------------------*\\
  =========                 |
  \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox
   \\\\    /   O peration     | Website:  https://openfoam.org
    \\\\  /    A nd           | Version:  dev
     \\\\/     M anipulation  |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    format      ascii;
    class       {cls};
    {loc}object      {obj};
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

"""


def _hdr(cls: str, obj: str, location: str | None = None) -> str:
    loc = f'location    "{location}";\n    ' if location else ""
    return _HEADER.format(cls=cls, obj=obj, loc=loc)


def _vec(v) -> str:
    return "({:.6g} {:.6g} {:.6g})".format(*v)
