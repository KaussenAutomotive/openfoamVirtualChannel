"""Manueller End-to-End-Test: baut einen Fall mit einem Testfahrzeug (abgerundeter Quader)."""
import sys
from pathlib import Path

import pyvista as pv

from windtunnel.case_builder import build_case
from windtunnel.geometry import Placement, analyze, place_vehicle
from windtunnel.settings import TunnelSettings

out = Path(sys.argv[1])
body = pv.ParametricSuperEllipsoid(xradius=2.2, yradius=0.9, zradius=0.6, n1=0.5, n2=0.5).triangulate().clean()
raw = body
mesh = place_vehicle(raw, Placement(ground_clearance=0.1))
info = analyze(mesh)
print(info)
s = TunnelSettings(cores=8, case_name="testcar", quality="Entwurf", iterations=150)
ci = build_case(out, mesh, info, s)
print(ci)
