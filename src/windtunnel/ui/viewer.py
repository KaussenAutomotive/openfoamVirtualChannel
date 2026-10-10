from __future__ import annotations

import numpy as np
import pyvista as pv
from pyvistaqt import QtInteractor

from ..case_builder import Domain
from ..results import FlowResults

DISPLAY_MODES = ["Geometrie", "Oberflaechendruck (cp)", "Oberflaechengeschwindigkeit"]
SPEED_CMAP = "jet"  # blau = langsam, gruen/gelb = Anstroemung, rot = schnell
SPEED_MAX = 1.6  # Farbskala bis SPEED_MAX * Anstroemgeschwindigkeit

VIEW_DIRECTIONS = {
    "iso": ((-1.0, -0.9, 0.45), (0, 0, 1)),
    "side": ((0.0, -1.0, 0.0), (0, 0, 1)),
    "front": ((-1.0, 0.0, 0.08), (0, 0, 1)),
    "top": ((0.0, 0.0, 1.0), (0, 1, 0)),
}


class TunnelViewer(QtInteractor):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.set_background("#2b3038", top="#000000")
        self.enable_anti_aliasing("msaa", multi_samples=4)
        self.add_axes()
        self._vehicle: pv.PolyData | None = None
        self._domain: Domain | None = None
        self._flow: FlowResults | None = None
        self._stream_cache: tuple | None = None
        self._view = "iso"
        self.mode = DISPLAY_MODES[0]
        self.show_domain = False
        self.show_slices = True
        self.show_streams = True
        self.show_slice_y = False
        self.show_slice_z = False
        self.stream_width = 1.5  # Radius = 0.0015 * Fahrzeuglaenge * Faktor
        self.stream_density = 0.4  # Anteil der dargestellten Stromlinien

    # ---------- Daten ----------
    def set_vehicle(self, mesh: pv.PolyData | None, reset_camera: bool = False) -> None:
        self._vehicle = mesh
        self._stream_cache = None
        self.refresh(reset_camera)

    def set_domain(self, domain: Domain | None) -> None:
        self._domain = domain
        self.refresh()

    def set_flow(self, flow: FlowResults | None) -> None:
        self._flow = flow
        self._stream_cache = None
        self.refresh(reset_camera=True)

    # ---------- Blickfeld, skaliert mit dem Modell ----------
    def _frame_bounds(self) -> tuple[float, ...] | None:
        if self._vehicle is None:
            return None
        b = self._vehicle.bounds
        length = b[1] - b[0]
        if self._flow is not None:
            return (b[0] - 0.3 * length, b[1] + 0.9 * length, b[2] - 0.35 * length,
                    b[3] + 0.35 * length, 0.0, b[5] + 0.3 * length)
        return (b[0] - 0.1 * length, b[1] + 0.1 * length, b[2] - 0.1 * length,
                b[3] + 0.1 * length, 0.0, b[5] + 0.1 * length)

    def _flow_region(self) -> tuple[float, ...] | None:
        """Bereich, in dem Stromlinien und Schnitte dargestellt werden."""
        if self._vehicle is None:
            return None
        b = self._vehicle.bounds
        length = b[1] - b[0]
        return (b[0] - 0.6 * length, b[1] + 1.8 * length, b[2] - 0.6 * length,
                b[3] + 0.6 * length, -0.05, b[5] + 0.6 * length)

    def _streams(self, flow: FlowResults) -> pv.PolyData | None:
        region = self._flow_region()
        if flow.streamlines is None or region is None:
            return None
        b = self._vehicle.bounds
        radius = 0.0015 * (b[1] - b[0]) * self.stream_width
        key = (id(flow.streamlines), region, round(radius, 6), round(self.stream_density, 3))
        if self._stream_cache and self._stream_cache[0] == key:
            return self._stream_cache[1]
        lines = flow.streamlines
        if self.stream_density < 1.0 and lines.n_cells > 1:
            n = max(1, int(lines.n_cells * self.stream_density))
            idx = np.sort(np.random.default_rng(7).choice(lines.n_cells, n, replace=False))
            lines = lines.extract_cells(idx).extract_geometry()
        clipped = lines.clip_box(bounds=region, invert=False).extract_geometry()
        tubes = clipped.tube(radius=radius, n_sides=8) if clipped.n_lines else None
        self._stream_cache = (key, tubes)
        return tubes

    # ---------- Darstellung ----------
    def refresh(self, reset_camera: bool = False) -> None:
        for name in ("vehicle", "domain", "ground", "slice_yNormal", "slice_zNormal", "streams"):
            self.remove_actor(name, render=False)
        for title in list(self.scalar_bars.keys()):
            self.remove_scalar_bar(title, render=False)

        flow = self._flow
        speed = flow.speed if flow else 1.0
        clim = (0.0, SPEED_MAX * speed)

        if self._domain is not None and self.show_domain:
            d = self._domain
            box = pv.Box(bounds=(d.xmin, d.xmax, d.ymin, d.ymax, d.zmin, d.zmax))
            self.add_mesh(box, style="wireframe", color="#8fa3b8", opacity=0.5, name="domain",
                          line_width=1, reset_camera=False)

        if self._vehicle is not None:
            b = self._vehicle.bounds
            length = b[1] - b[0]
            ground = pv.Plane(center=((b[0] + b[1]) / 2 + 0.5 * length, (b[2] + b[3]) / 2, -1e-3),
                              direction=(0, 0, 1), i_size=4.0 * length, j_size=1.6 * length)
            self.add_mesh(ground, color="#3a3f47", name="ground", reset_camera=False,
                          specular=0.3, ambient=0.3)

        surface = flow.surface if flow and flow.surface is not None else None
        if self.mode != DISPLAY_MODES[0] and surface is not None:
            if self.mode == DISPLAY_MODES[1]:
                scalars, cmap, sclim, title = "cp", "coolwarm", (-1.0, 1.0), "cp"
            else:
                scalars, cmap, sclim, title = "Umag", SPEED_CMAP, clim, "|U| [m/s]"
            if scalars in surface.point_data or scalars in surface.cell_data:
                self.add_mesh(surface, scalars=scalars, cmap=cmap, clim=sclim, name="vehicle",
                              smooth_shading=True, reset_camera=False,
                              scalar_bar_args={"title": title, "color": "white", "vertical": True})
        elif self._vehicle is not None:
            self.add_mesh(self._vehicle, color="#eef1f4", name="vehicle", smooth_shading=True,
                          split_sharp_edges=True, feature_angle=40, specular=0.6, specular_power=30,
                          ambient=0.25, diffuse=0.8, reset_camera=False)

        region = self._flow_region()
        if flow and self.show_slices and region is not None:
            for key, enabled in (("yNormal", self.show_slice_y), ("zNormal", self.show_slice_z)):
                s = flow.slices.get(key)
                if s is not None and enabled and "Umag" in s.point_data:
                    s = s.clip_box(bounds=region, invert=False)
                    self.add_mesh(s, scalars="Umag", cmap=SPEED_CMAP, clim=clim, name=f"slice_{key}",
                                  opacity=0.85, reset_camera=False, show_scalar_bar=False)

        if flow and self.show_streams:
            tubes = self._streams(flow)
            if tubes is not None and "Umag" in tubes.point_data:
                self.add_mesh(tubes, scalars="Umag", cmap=SPEED_CMAP, clim=clim, name="streams",
                              smooth_shading=True, reset_camera=False, specular=0.4,
                              scalar_bar_args={"title": "|U| [m/s]", "color": "white", "vertical": True,
                                               "n_labels": 5, "fmt": "%.0f"})

        if reset_camera:
            self.set_view(self._view)
        else:
            self.render()

    # ---------- Kamera ----------
    def set_view(self, name: str) -> None:
        self._view = name
        frame = self._frame_bounds()
        if frame is None:
            self.view_isometric()
            return
        direction, up = VIEW_DIRECTIONS[name]
        center = np.array([(frame[0] + frame[1]) / 2, (frame[2] + frame[3]) / 2, (frame[4] + frame[5]) / 2])
        d = np.array(direction, dtype=float)
        d /= np.linalg.norm(d)
        self.camera.focal_point = tuple(center)
        self.camera.position = tuple(center + d * 10.0)
        self.camera.up = up
        self.reset_camera(bounds=frame)
        self.render()

    def view_iso(self) -> None:
        self.set_view("iso")

    def view_side(self) -> None:
        self.set_view("side")

    def view_front(self) -> None:
        self.set_view("front")

    def view_top(self) -> None:
        self.set_view("top")
