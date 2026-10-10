"""Manueller Wellen-Test (VoF): python manual_waves.py <fall-ordner> <tiefgang_m> <bodenabstand_m> <km/h> <iter>"""
import sys
from pathlib import Path

import pyvista as pv

from windtunnel.case_builder import build_case
from windtunnel.geometry import Placement, analyze, place_vehicle
from windtunnel.settings import TunnelSettings
from windtunnel.vof_case import froude_numbers

out = Path(sys.argv[1])
draft, clearance, kmh, iters = float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4]), int(sys.argv[5])
raw = pv.ParametricSuperEllipsoid(xradius=2.2, yradius=0.5, zradius=0.5, n1=1.0, n2=1.0).triangulate().clean()
mesh = place_vehicle(raw, Placement(ground_clearance=clearance))
info = analyze(mesh, mesh.bounds[4] + draft)
s = TunnelSettings(medium="Wasser", free_surface=True, speed_kmh=kmh, draft=draft, cores=8,
                   case_name="testwaves", iterations=iters)
ci = build_case(out, mesh, info, s)
print(info.immersion, ci.domain, ci.est_cells, froude_numbers(info, s))
