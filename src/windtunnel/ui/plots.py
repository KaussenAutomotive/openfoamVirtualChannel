from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ..results import Coefficients

_COLORS = {"Ux": "#e06c75", "Uy": "#98c379", "Uz": "#61afef", "p": "#e5c07b", "nuTilda": "#c678dd"}


class PlotsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        pg.setConfigOptions(antialias=True, background="#1b1f24", foreground="#c8d0da")
        self.res_plot = pg.PlotWidget(title="Residuen")
        self.res_plot.setLogMode(y=True)
        self.res_plot.setLabel("bottom", "Iteration")
        self.res_plot.addLegend()
        self.res_plot.showGrid(x=True, y=True, alpha=0.25)
        self.coef_plot = pg.PlotWidget(title="Beiwerte")
        self.coef_plot.setLabel("bottom", "Iteration")
        self.coef_plot.addLegend()
        self.coef_plot.showGrid(x=True, y=True, alpha=0.25)
        lay = QVBoxLayout(self)
        lay.addWidget(self.res_plot)
        lay.addWidget(self.coef_plot)
        self._res_curves: dict[str, pg.PlotDataItem] = {}
        self._res_data: dict[str, tuple[list[int], list[float]]] = {}

    def clear(self) -> None:
        self.res_plot.clear()
        self.coef_plot.clear()
        self._res_curves.clear()
        self._res_data.clear()

    def add_residual(self, iteration: int, name: str, value: float) -> None:
        xs, ys = self._res_data.setdefault(name, ([], []))
        xs.append(iteration)
        ys.append(max(value, 1e-12))
        curve = self._res_curves.get(name)
        if curve is None:
            curve = self.res_plot.plot(name=name, pen=pg.mkPen(_COLORS.get(name, "#abb2bf"), width=2))
            self._res_curves[name] = curve
        curve.setData(xs, ys)

    def show_coefficients(self, coeffs: Coefficients | None) -> None:
        self.coef_plot.clear()
        if coeffs is None:
            return
        for name, color in (("Cd", "#e06c75"), ("Cl", "#61afef")):
            if name in coeffs.values:
                self.coef_plot.plot(coeffs.time, coeffs.values[name], name=name,
                                    pen=pg.mkPen(color, width=2))
