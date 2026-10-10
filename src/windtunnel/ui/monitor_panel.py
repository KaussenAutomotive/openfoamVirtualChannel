from __future__ import annotations

from collections import deque

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..monitor import CpuSample

_HISTORY = 120


class MonitorPanel(QWidget):
    toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.toggle = QPushButton("Live-Auslastung")
        self.toggle.setCheckable(True)
        self.toggle.toggled.connect(self.toggled)
        self.label = QLabel("Keine Daten. Die Anzeige startet automatisch mit der Simulation.")
        self.label.setStyleSheet("font-size: 14px; padding: 4px;")

        self.cores = pg.PlotWidget(title="Auslastung je CPU-Kern (WSL)")
        self.cores.setYRange(0, 100)
        self.cores.setLabel("left", "%")
        self.cores.setLabel("bottom", "Kern")
        self.cores.showGrid(y=True, alpha=0.25)
        self._bars: pg.BarGraphItem | None = None

        self.history = pg.PlotWidget(title="Gesamt-CPU und Speicher")
        self.history.setYRange(0, 100)
        self.history.setLabel("left", "%")
        self.history.setLabel("bottom", "Sekunden")
        self.history.addLegend()
        self.history.showGrid(x=True, y=True, alpha=0.25)
        self._cpu_curve = self.history.plot(name="CPU", pen=pg.mkPen("#e06c75", width=2))
        self._mem_curve = self.history.plot(name="RAM", pen=pg.mkPen("#61afef", width=2))
        self._cpu_hist: deque[float] = deque(maxlen=_HISTORY)
        self._mem_hist: deque[float] = deque(maxlen=_HISTORY)

        top = QHBoxLayout()
        top.addWidget(self.toggle)
        top.addWidget(self.label, 1)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.cores, 1)
        lay.addWidget(self.history, 1)

    def set_active(self, active: bool) -> None:
        self.toggle.blockSignals(True)
        self.toggle.setChecked(active)
        self.toggle.blockSignals(False)

    def clear(self) -> None:
        self._cpu_hist.clear()
        self._mem_hist.clear()
        self._cpu_curve.setData([], [])
        self._mem_curve.setData([], [])

    def update_sample(self, s: CpuSample) -> None:
        n = len(s.cores)
        x = np.arange(n)
        colors = [pg.mkBrush("#e06c75" if v > 85 else "#e5c07b" if v > 50 else "#98c379") for v in s.cores]
        if self._bars is not None:
            self.cores.removeItem(self._bars)
        self._bars = pg.BarGraphItem(x=x, height=s.cores, width=0.7, brushes=colors)
        self.cores.addItem(self._bars)
        self.cores.setXRange(-0.7, n - 0.3)

        mem_pct = 100.0 * s.mem_used_gb / s.mem_total_gb if s.mem_total_gb else 0.0
        self._cpu_hist.append(s.total)
        self._mem_hist.append(mem_pct)
        xs = np.arange(-len(self._cpu_hist) + 1, 1)
        self._cpu_curve.setData(xs, list(self._cpu_hist))
        self._mem_curve.setData(xs, list(self._mem_hist))
        self.label.setText(f"CPU gesamt: {s.total:.0f} %  |  {n} Kerne  |  "
                           f"RAM: {s.mem_used_gb:.1f} / {s.mem_total_gb:.1f} GB ({mem_pct:.0f} %)")
