"""Manueller Wasserkanal-Test: python manual_water.py <fall-ordner> <tiefgang_m> <bodenabstand_m>"""
import sys
from pathlib import Path

import pyvista as pv

from windtunnel.case_builder import build_case
from windtunnel.geometry import Placement, analyze, place_vehicle
from windtunnel.settings import TunnelSettings

out, draft, clearance = Path(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3])
raw = pv.ParametricSuperEllipsoid(xradius=2.2, yradius=0.5, zradius=0.5, n1=1.0, n2=1.0).triangulate().clean()
mesh = place_vehicle(raw, Placement(ground_clearance=clearance))
info = analyze(mesh, mesh.bounds[4] + draft)
s = TunnelSettings(medium="Wasser", speed_kmh=18, draft=draft, rolling_road=False, cores=8,
                   case_name="testwater", iterations=100)
ci = build_case(out, mesh, info, s)
print(info.immersion, ci.domain, ci.est_cells)
