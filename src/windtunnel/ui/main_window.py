from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pyvista as pv
from PySide6.QtCore import QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGroupBox, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QScrollArea, QSlider, QSpinBox, QTabWidget, QToolBar, QVBoxLayout, QWidget,
)

from .. import __version__
from ..case_builder import blockage_ratio, build_case, compute_domain, estimate_cells
from ..geometry import (NATIVE_UNIT_LABEL, UNIT_CHOICES, Placement, VehicleInfo, analyze,
                        guess_unit_label, place_vehicle)
from ..importers import FILE_FILTER, SUPPORTED_EXTENSIONS, GeometryImportError, ImportResult, load_geometry
from ..results import FlowResults, load_results
from ..monitor import CpuSample, SystemMonitor
from ..runner import STEPS, WslRunner, check_wsl
from ..settings import MEDIA, QUALITY_PRESETS, RHO_WATER, TunnelSettings, WslConfig
from ..timing import RunTimer, format_duration
from ..vof_case import froude_numbers
from .monitor_panel import MonitorPanel
from .plots import PlotsPanel
from .viewer import DISPLAY_MODES, TunnelViewer


class Worker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args):
        super().__init__()
        self._fn, self._args = fn, args

    def run(self) -> None:
        try:
            self.done.emit(self._fn(*self._args))
        except GeometryImportError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - Fehler an die GUI melden
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class SettingsDialog(QDialog):
    def __init__(self, wsl: WslConfig, case_root: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Einstellungen")
        self.distro = QLineEdit(wsl.distro)
        self.bashrc = QLineEdit(wsl.bashrc)
        self.work = QLineEdit(wsl.work_dir)
        self.root = QLineEdit(str(case_root))
        self.status = QLabel("")
        self.status.setWordWrap(True)
        test = QPushButton("OpenFOAM in WSL pruefen")
        test.clicked.connect(self._test)
        form = QFormLayout()
        form.addRow("WSL-Distribution", self.distro)
        form.addRow("OpenFOAM bashrc", self.bashrc)
        form.addRow("Arbeitsordner in WSL", self.work)
        form.addRow("Fall-Ordner (Windows)", self.root)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(test)
        lay.addWidget(self.status)
        lay.addWidget(buttons)
        self.resize(520, 260)

    def config(self) -> WslConfig:
        return WslConfig(self.distro.text().strip(), self.bashrc.text().strip(), self.work.text().strip())

    def _test(self) -> None:
        self.status.setText("Pruefe ...")
        QApplication.processEvents()
        ok, msg = check_wsl(self.config())
        self.status.setText(("OK: " if ok else "Fehler: ") + msg)


def _dspin(lo, hi, val, step=1.0, dec=1, suffix="") -> QDoubleSpinBox:
    w = QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setValue(val)
    w.setSingleStep(step)
    w.setDecimals(dec)
    w.setSuffix(suffix)
    return w


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Virtueller Wind- und Wasserkanal {__version__}")
        self.resize(1450, 900)
        self.setAcceptDrops(True)

        self.qs = QSettings("windtunnel", "windtunnel")
        self.wsl = WslConfig(
            self.qs.value("wsl/distro", WslConfig.distro),
            self.qs.value("wsl/bashrc", WslConfig.bashrc),
            self.qs.value("wsl/work_dir", WslConfig.work_dir),
        )
        self.case_root = Path(self.qs.value("case_root", str(Path.home() / "WindTunnelCases")))

        self.raw: pv.PolyData | None = None
        self.source: Path | None = None
        self.vehicle: pv.PolyData | None = None
        self.info: VehicleInfo | None = None
        self.flow: FlowResults | None = None
        self.case_dir: Path | None = None
        self._worker: Worker | None = None
        self._runner = WslRunner(self)
        self._runner.log.connect(self._append_log)
        self._runner.step.connect(self._on_step)
        self._runner.residual.connect(lambda i, n, v: self.plots.add_residual(i, n, v))
        self._runner.progress.connect(self._on_progress)
        self._runner.finished.connect(self._on_run_finished)
        self.run_timer = RunTimer()
        self.monitor = SystemMonitor(self)
        self.monitor.sample.connect(self._on_cpu_sample)
        self.monitor.failed.connect(lambda m: self.statusBar().showMessage(m))
        self._clock = QTimer(self, interval=1000)
        self._clock.timeout.connect(self._tick)
        self._debounce = QTimer(self, singleShot=True, interval=350)
        self._debounce.timeout.connect(self._update_vehicle)

        self._build_ui()
        self._update_estimates()

    # ---------- UI ----------
    def _build_ui(self) -> None:
        self.viewer = TunnelViewer(self)
        self.plots = PlotsPanel()
        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumBlockCount(20000)
        self.log.setStyleSheet("font-family: Consolas, monospace;")
        self.result_label = QLabel("Noch keine Ergebnisse.")
        self.result_label.setStyleSheet("font-size: 15px; padding: 6px;")

        self.tabs = QTabWidget()
        view_tab = QWidget()
        vl = QVBoxLayout(view_tab)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.addWidget(self.viewer)
        vl.addWidget(self.result_label)
        self.tabs.addTab(view_tab, "3D-Ansicht")
        self.tabs.addTab(self.plots, "Residuen / Beiwerte")
        self.monitor_panel = MonitorPanel()
        self.monitor_panel.toggled.connect(self._toggle_monitor)
        self.tabs.addTab(self.monitor_panel, "Auslastung")
        self.tabs.addTab(self.log, "Protokoll")
        self.setCentralWidget(self.tabs)

        side = QWidget()
        sl = QVBoxLayout(side)
        sl.addWidget(self._geometry_group())
        sl.addWidget(self._tunnel_group())
        sl.addWidget(self._sim_group())
        sl.addWidget(self._result_group())
        sl.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(side)
        scroll.setMinimumWidth(360)
        from PySide6.QtWidgets import QDockWidget
        dock = QDockWidget("Einstellungen", self)
        dock.setWidget(scroll)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

        tb = QToolBar("Aktionen")
        tb.setMovable(False)
        self.addToolBar(tb)
        self.act_open = QAction("Geometrie oeffnen ...", self, triggered=self.open_dialog)
        self.act_run = QAction("Simulation starten", self, triggered=self.start_simulation)
        self.act_stop = QAction("Abbrechen", self, triggered=self._runner.cancel)
        self.act_stop.setEnabled(False)
        self.act_load = QAction("Ergebnisse laden ...", self, triggered=self.load_results_dialog)
        self.act_folder = QAction("Fall-Ordner oeffnen", self, triggered=self.open_case_folder)
        self.act_settings = QAction("Einstellungen ...", self, triggered=self.open_settings)
        for a in (self.act_open, self.act_run, self.act_stop, self.act_load, self.act_folder, self.act_settings):
            tb.addAction(a)
        tb.addSeparator()
        for label, fn in (("Iso", self.viewer.view_iso), ("Seite", self.viewer.view_side),
                          ("Front", self.viewer.view_front), ("Oben", self.viewer.view_top)):
            tb.addAction(QAction(label, self, triggered=lambda _=False, f=fn: f()))

        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.progress.setVisible(False)
        self.time_label = QLabel("")
        self.cpu_label = QLabel("")
        self.statusBar().addPermanentWidget(self.cpu_label)
        self.statusBar().addPermanentWidget(self.time_label)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().showMessage("Geometrie per Menue oder Drag & Drop laden (STL, OBJ, STEP, 3MF).")

    def _geometry_group(self) -> QGroupBox:
        g = QGroupBox("Geometrie")
        f = QFormLayout(g)
        self.file_label = QLabel("-")
        self.file_label.setWordWrap(True)
        btn = QPushButton("Datei waehlen ...")
        btn.clicked.connect(self.open_dialog)
        self.unit = QComboBox()
        self.unit.addItems(list(UNIT_CHOICES))
        self.unit.setCurrentText("Meter")
        self.rot = [_dspin(-360, 360, 0, 90, 0, " deg") for _ in range(3)]
        self.clearance = _dspin(0, 2000, 0, 5, 0, " mm")
        self.step_detail = QComboBox()
        self.step_detail.addItems(["Grob", "Normal", "Fein"])
        self.step_detail.setCurrentIndex(1)
        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        f.addRow(btn)
        f.addRow("Datei", self.file_label)
        f.addRow("Einheit", self.unit)
        for axis, w in zip("XYZ", self.rot):
            f.addRow(f"Drehung um {axis}", w)
        f.addRow("Bodenabstand", self.clearance)
        f.addRow("STEP-Netzfeinheit", self.step_detail)
        f.addRow(self.info_label)
        for w in (self.unit, self.clearance, *self.rot):
            sig = w.currentIndexChanged if isinstance(w, QComboBox) else w.valueChanged
            sig.connect(lambda *_: self._debounce.start())
        return g

    def _tunnel_group(self) -> QGroupBox:
        g = QGroupBox("Kanal")
        f = QFormLayout(g)
        self.medium = QComboBox()
        self.medium.addItem("Luft (Windkanal)", "Luft")
        self.medium.addItem("Wasser (Wasserkanal)", "Wasser")
        self.medium.currentIndexChanged.connect(self._on_medium_changed)
        f.addRow("Medium", self.medium)
        self.draft = _dspin(0.01, 1000, 1.0, 0.1, 2, " m")
        self.draft_slider = QSlider(Qt.Orientation.Horizontal)
        self.draft_slider.setRange(0, 1000)
        self.waterline_info = QLabel("")
        self.waterline_info.setWordWrap(True)
        f.addRow("Wasserlinie ueber Kiel", self.draft)
        f.addRow(self.draft_slider)
        self.chk_vof = QCheckBox("Freie Oberflaeche mit Wellen (Bugwelle, VoF)")
        self.chk_vof.setToolTip("Zweiphasig Wasser/Luft, instationaer und deutlich rechenintensiver als der starre Deckel.")
        self.chk_vof.toggled.connect(self._on_vof_toggled)
        f.addRow(self.chk_vof)
        f.addRow(self.waterline_info)
        self._tunnel_form = f
        self._water_rows = (self.draft, self.draft_slider, self.chk_vof, self.waterline_info)
        for w in self._water_rows:
            f.setRowVisible(w, False)
        self.draft.valueChanged.connect(self._on_draft_changed)
        self.draft_slider.valueChanged.connect(self._on_draft_slider)
        self.speed = _dspin(0.5, 400, 100, 5, 1, " km/h")
        self.rolling = QCheckBox("Rollende Strasse (bewegter Boden)")
        self.rolling.setChecked(True)
        self.up = _dspin(1, 10, 3, 0.5, 1, " L")
        self.down = _dspin(2, 15, 6, 0.5, 1, " L")
        self.side = _dspin(1.5, 10, 3, 0.5, 1, " B")
        self.top = _dspin(2, 10, 4, 0.5, 1, " H")
        self.tunnel_info = QLabel("")
        f.addRow("Geschwindigkeit", self.speed)
        f.addRow(self.rolling)
        f.addRow("Strecke vor Fahrzeug", self.up)
        f.addRow("Strecke hinter Fahrzeug", self.down)
        f.addRow("Halbbreite (x Fahrzeugbreite)", self.side)
        f.addRow("Hoehe (x Fahrzeughoehe)", self.top)
        f.addRow(self.tunnel_info)
        for w in (self.up, self.down, self.side, self.top, self.speed):
            w.valueChanged.connect(lambda *_: self._update_estimates())
        return g

    def _on_medium_changed(self) -> None:
        water = self.medium.currentData() == "Wasser"
        for w in self._water_rows:
            self._tunnel_form.setRowVisible(w, water)
        self.top.setEnabled(not water)  # Wasserkanal: Hoehe = Wasserlinie (bzw. Wasserlinie + Luft)
        self.viewer.set_medium("Wasser" if water else "Luft")
        self.chk_domain.setText("Wasserkanal-Box" if water else "Windkanal-Box")
        if water and self.speed.value() > 30:
            self.speed.setValue(10)  # Wasser: typisch wenige m/s
        elif not water and self.speed.value() < 30:
            self.speed.setValue(100)
        if water and self.info is not None:
            self.draft.setValue(max(0.5 * self.info.height, 0.01))
            if self.clearance.value() == 0:
                self.clearance.setValue(round(0.5 * self.info.height * 1000))
        self._update_vehicle()

    def _on_vof_toggled(self, on: bool) -> None:
        self.rolling.setEnabled(not on)  # VoF: Kanalboden ist eine Gleitwand
        self._update_vehicle()

    def _on_draft_changed(self) -> None:
        h = self.info.height if self.info else 1.0
        self.draft_slider.blockSignals(True)
        self.draft_slider.setValue(min(1000, round(1000 * self.draft.value() / (6 * h))))
        self.draft_slider.blockSignals(False)
        self._debounce.start()

    def _on_draft_slider(self, v: int) -> None:
        h = self.info.height if self.info else 1.0
        self.draft.setValue(max(0.01, v / 1000 * 6 * h))

    def _sim_group(self) -> QGroupBox:
        g = QGroupBox("Netz und Solver")
        f = QFormLayout(g)
        self.quality = QComboBox()
        self.quality.addItems(list(QUALITY_PRESETS))
        self.iters = QSpinBox()
        self.iters.setRange(0, 20000)
        self.iters.setSpecialValueText("Automatisch")
        self.iters.setSingleStep(100)
        self.cores = QSpinBox()
        self.cores.setRange(2, 128)
        self.cores.setValue(int(self.qs.value("cores", 8)))
        self.seeds = QSpinBox()
        self.seeds.setRange(0, 30)
        self.seeds.setValue(9)
        self.cells_label = QLabel("")
        f.addRow("Qualitaet", self.quality)
        f.addRow("Iterationen", self.iters)
        f.addRow("CPU-Kerne", self.cores)
        f.addRow("Stromlinien-Startlinien", self.seeds)
        f.addRow("Zellen (Schaetzung)", self.cells_label)
        self.quality.currentIndexChanged.connect(lambda *_: self._update_estimates())
        run = QPushButton("Fall erzeugen und rechnen")
        run.clicked.connect(self.start_simulation)
        f.addRow(run)
        return g

    def _result_group(self) -> QGroupBox:
        g = QGroupBox("Ergebnisanzeige")
        f = QFormLayout(g)
        self.mode = QComboBox()
        self.mode.addItems(DISPLAY_MODES)
        self.chk_domain = QCheckBox("Windkanal-Box")
        self.chk_slice_y = QCheckBox("Schnitt Seitenansicht (y)")
        self.chk_slice_z = QCheckBox("Schnitt Draufsicht (z)")
        self.chk_streams = QCheckBox("Stromlinien")
        self.chk_streams.setChecked(True)
        self.chk_waves = QCheckBox("Wasseroberflaeche mit Wellen")
        self.chk_waves.setChecked(True)
        self.stream_width = _dspin(0.3, 6, 1.5, 0.3, 1, "x")
        self.stream_width.valueChanged.connect(self._apply_display)
        self.stream_density = _dspin(5, 100, 40, 5, 0, " %")
        self.stream_density.valueChanged.connect(self._apply_display)
        f.addRow("Fahrzeug", self.mode)
        f.addRow("Stromlinien-Dicke", self.stream_width)
        f.addRow("Stromlinien-Dichte", self.stream_density)
        for c in (self.chk_domain, self.chk_slice_y, self.chk_slice_z, self.chk_streams, self.chk_waves):
            f.addRow(c)
            c.toggled.connect(self._apply_display)
        self.mode.currentIndexChanged.connect(self._apply_display)
        return g

    def _apply_display(self) -> None:
        v = self.viewer
        v.mode = self.mode.currentText()
        v.show_domain = self.chk_domain.isChecked()
        v.show_slice_y = self.chk_slice_y.isChecked()
        v.show_slice_z = self.chk_slice_z.isChecked()
        v.show_streams = self.chk_streams.isChecked()
        v.show_waves = self.chk_waves.isChecked()
        v.stream_width = self.stream_width.value()
        v.stream_density = self.stream_density.value() / 100.0
        v.refresh()

    # ---------- Einstellungen ----------
    def settings(self) -> TunnelSettings:
        name = re.sub(r"[^A-Za-z0-9_-]+", "_", self.source.stem if self.source else "fahrzeug").strip("_") or "fahrzeug"
        return TunnelSettings(
            speed_kmh=self.speed.value(), rolling_road=self.rolling.isChecked(),
            quality=self.quality.currentText(), cores=self.cores.value(), iterations=self.iters.value(),
            upstream=self.up.value(), downstream=self.down.value(), side=self.side.value(),
            top=self.top.value(), streamline_seeds=self.seeds.value(), case_name=name, wsl=self.wsl,
            medium=self.medium.currentData(), draft=self.draft.value(),
            free_surface=self.chk_vof.isChecked(),
        )

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.wsl, self.case_root, self)
        if dlg.exec():
            self.wsl = dlg.config()
            self.case_root = Path(dlg.root.text().strip() or str(self.case_root))
            self.qs.setValue("wsl/distro", self.wsl.distro)
            self.qs.setValue("wsl/bashrc", self.wsl.bashrc)
            self.qs.setValue("wsl/work_dir", self.wsl.work_dir)
            self.qs.setValue("case_root", str(self.case_root))

    # ---------- Geometrie ----------
    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls() and any(u.toLocalFile().lower().endswith(SUPPORTED_EXTENSIONS)
                                          for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        for u in e.mimeData().urls():
            if u.toLocalFile().lower().endswith(SUPPORTED_EXTENSIONS):
                self.open_file(u.toLocalFile())
                break

    def open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Geometrie oeffnen", str(Path.home()), FILE_FILTER)
        if path:
            self.open_file(path)

    def open_file(self, path: str) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        detail = {"Grob": 0.5, "Normal": 1.0, "Fein": 2.0}[self.step_detail.currentText()]
        self.statusBar().showMessage(f"Lade {Path(path).name} ...")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self._worker = Worker(load_geometry, path, detail)
        self._worker.done.connect(self._on_loaded)
        self._worker.failed.connect(self._on_load_failed)
        self._worker.start()

    def _on_load_failed(self, msg: str) -> None:
        QApplication.restoreOverrideCursor()
        self.statusBar().showMessage("Import fehlgeschlagen.")
        QMessageBox.critical(self, "Import fehlgeschlagen", msg)

    def _on_loaded(self, res: ImportResult) -> None:
        QApplication.restoreOverrideCursor()
        self.raw, self.source = res.mesh, res.source
        self.file_label.setText(res.source.name)
        label = NATIVE_UNIT_LABEL.get(res.native_unit or "", None) or guess_unit_label(res.mesh)
        self.unit.blockSignals(True)
        self.unit.setCurrentText(label)
        self.unit.blockSignals(False)
        self.flow = None
        self.viewer.set_flow(None)
        self.result_label.setText("Noch keine Ergebnisse.")
        self._update_vehicle(reset_camera=True)
        if self.medium.currentData() == "Wasser":
            self.draft.setValue(max(0.5 * self.info.height, 0.01))
        self.statusBar().showMessage(f"{res.source.name} geladen ({res.mesh.n_cells} Dreiecke).")

    def _update_vehicle(self, reset_camera: bool = False) -> None:
        if self.raw is None:
            return
        p = Placement(UNIT_CHOICES[self.unit.currentText()], *(w.value() for w in self.rot),
                      self.clearance.value() / 1000.0)
        self.vehicle = place_vehicle(self.raw, p)
        water = self.medium.currentData() == "Wasser"
        waterline = self.vehicle.bounds[4] + self.draft.value() if water else None
        self.info = analyze(self.vehicle, waterline)
        i = self.info
        warn = "" if i.watertight else (
            f"<br><b style='color:#d9822b'>Netz nicht wasserdicht ({i.open_edges} offene Kanten) - "
            "snappyHexMesh kann fehlschlagen.</b>")
        self.info_label.setText(
            f"L x B x H: {i.length:.2f} x {i.width:.2f} x {i.height:.2f} m<br>"
            f"Stirnflaeche: {i.frontal_area:.3f} m2, Dreiecke: {i.n_triangles}{warn}")
        self.viewer.set_waterline(waterline, refresh=False)
        self.viewer.set_vehicle(self.vehicle, reset_camera)
        self._update_estimates()

    def _update_estimates(self) -> None:
        if self.info is None:
            self.cells_label.setText("-")
            self.tunnel_info.setText("")
            return
        s = self.settings()
        self.viewer.set_domain(compute_domain(self.info, s))
        cells = estimate_cells(self.info, s)
        self.cells_label.setText(f"ca. {cells / 1e6:.2f} Mio.")
        self.tunnel_info.setText(
            f"Blockierung: {100 * blockage_ratio(self.info, s):.1f} %   |   "
            f"Re = {s.speed_ms * self.info.length / s.nu:.2e}"
            + ("<br>Hinweis: ohne freie Oberflaeche (starrer Deckel)" if s.medium == "Wasser" else ""))
        self._update_waterline_info(s)

    def _update_waterline_info(self, s: TunnelSettings) -> None:
        i = self.info
        if not s.is_water or i is None or i.waterline is None:  # info wird nach Medienwechsel neu berechnet
            self.waterline_info.setText("")
            return
        top_gap = i.waterline - i.bounds[5]
        if s.is_vof:
            fr, fr_h, lam = froude_numbers(i, s)
            part = (f"voll getaucht (Tauchtiefe {top_gap:.2f} m)" if top_gap >= 0
                    else f"{100 * i.immersion:.0f} % der Rumpfhoehe eingetaucht")
            text = (f"Freie Oberflaeche (VoF): {part}.<br>Froude-Zahl Fr = {fr:.2f}, "
                    f"Tiefen-Froude Fr_h = {fr_h:.2f}, Wellenlaenge ca. {lam:.1f} m."
                    "<br>Instationaere Zweiphasenrechnung, deutlich rechenintensiver.")
            if fr_h > 0.7:
                text += "<br><b style='color:#d9822b'>Flaches Wasser (Fr_h &gt; 0,7): Wassertiefe vergroessern.</b>"
            if fr < 0.1:
                text += "<br>Bei Fr &lt; 0,1 sind die Wellen sehr klein."
            self.waterline_info.setText(text)
            return
        if top_gap >= 0:
            text = (f"Voll getaucht: Tauchtiefe {top_gap:.2f} m "
                    f"({top_gap / i.height:.1f} x Rumpfhoehe), Wassertiefe {i.waterline:.2f} m.")
            if top_gap < i.height:
                text += ("<br><b style='color:#d9822b'>Oberflaeche nah: Wellenwiderstand wird nicht "
                         "erfasst, Ergebnis nur Naeherung.</b>")
        else:
            text = (f"Teilweise eingetaucht: {100 * i.immersion:.0f} % der Rumpfhoehe. "
                    "Rumpf wird an der Wasserlinie geschnitten (Doppelkoerper-Modell), "
                    "Wellen werden nicht berechnet.")
        self.waterline_info.setText(text)

    # ---------- Simulation ----------
    def start_simulation(self) -> None:
        if self.vehicle is None or self.info is None:
            QMessageBox.information(self, "Keine Geometrie", "Bitte zuerst eine Geometrie laden.")
            return
        if self._runner.running:
            return
        s = self.settings()
        self.qs.setValue("cores", s.cores)
        if not self.info.watertight and QMessageBox.question(
                self, "Netz nicht wasserdicht",
                "Das Fahrzeugnetz hat offene Kanten. Trotzdem fortfahren?") != QMessageBox.StandardButton.Yes:
            return
        self.case_dir = self.case_root / s.case_name
        try:
            ci = build_case(self.case_dir, self.vehicle, self.info, s)
        except OSError as exc:
            QMessageBox.critical(self, "Fall konnte nicht erzeugt werden", str(exc))
            return
        self.log.clear()
        self.plots.clear()
        self.act_run.setEnabled(False)
        self.act_stop.setEnabled(True)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(STEPS))
        self.progress.setValue(0)
        self.tabs.setCurrentWidget(self.plots)
        self._append_log(f"Fall erzeugt: {ci.path}")
        self.run_timer.start()
        self._clock.start()
        self.monitor_panel.clear()
        self.monitor_panel.set_active(True)
        self._toggle_monitor(True)
        self._tick()
        self._runner.start(ci.run_script, s)

    def _on_step(self, name: str, idx: int, total: int) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(idx)
        self.progress.setFormat(f"{name} (%v/%m)")
        self.statusBar().showMessage(f"Schritt {idx}/{total}: {name}")

    def _on_progress(self, it: int, total: int) -> None:
        self.run_timer.update_iteration(it, total)
        self.statusBar().showMessage(f"foamRun: Iteration {it}/{total}")

    def _tick(self) -> None:
        t = self.run_timer
        if not t.running:
            self.time_label.setText(f"Gesamtlaufzeit {format_duration(t.elapsed)}" if t.elapsed else "")
            return
        text = f"Laufzeit {format_duration(t.elapsed)}"
        eta = t.eta()
        if eta is not None:
            done = t.finish_time()
            text += f"  |  Restzeit max. {format_duration(eta)} (bis ca. {done:%H:%M})"
            s = t.seconds_per_iteration()
            if s:
                text += f"  |  {s:.2f} s/Iter."
        elif t.running:
            text += "  |  Restzeit wird mit Start der Iterationen geschaetzt"
        self.time_label.setText(text)

    def _toggle_monitor(self, on: bool) -> None:
        if on:
            self.monitor.start(self.wsl.distro)
        else:
            self.monitor.stop()
            self.cpu_label.setText("")

    def _on_cpu_sample(self, s: CpuSample) -> None:
        self.monitor_panel.update_sample(s)
        self.cpu_label.setText(f"CPU {s.total:.0f} %  |  RAM {s.mem_used_gb:.1f}/{s.mem_total_gb:.0f} GB")

    def _append_log(self, line: str) -> None:
        self.log.appendPlainText(line)

    def _on_run_finished(self, ok: bool, msg: str) -> None:
        self.act_run.setEnabled(True)
        self.act_stop.setEnabled(False)
        self.progress.setVisible(False)
        self.run_timer.stop()
        self._clock.stop()
        self._tick()
        self.monitor_panel.set_active(False)
        self._toggle_monitor(False)
        self._append_log(f"Gesamtlaufzeit: {format_duration(self.run_timer.elapsed)}")
        self.statusBar().showMessage(msg)
        if ok and self.case_dir is not None:
            self._load_results(self.case_dir / "results")
            self.tabs.setCurrentIndex(0)
        elif not ok:
            self.tabs.setCurrentWidget(self.log)
            QMessageBox.warning(self, "Simulation", msg + "\nDetails siehe Protokoll.")

    # ---------- Ergebnisse ----------
    def load_results_dialog(self) -> None:
        start = str(self.case_dir or self.case_root)
        d = QFileDialog.getExistingDirectory(self, "Ordner 'results' eines Falls waehlen", start)
        if d:
            p = Path(d)
            self._load_results(p / "results" if (p / "results").is_dir() else p)

    def _load_results(self, results_dir: Path) -> None:
        try:
            s = self.settings()
            flow = load_results(results_dir, s.speed_ms, RHO_WATER if s.is_vof else 1.0)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Ergebnisse", f"Ergebnisse konnten nicht geladen werden:\n{exc}")
            return
        self.flow = flow
        if self.vehicle is None and flow.surface is not None:
            self.viewer.set_vehicle(flow.surface)
        self.plots.show_coefficients(flow.coefficients)
        self.viewer.set_flow(flow)
        c = flow.coefficients
        if c is not None and c.cd is not None:
            cd, cl = c.averaged("Cd"), c.averaged("Cl")
            area = self.info.frontal_area if self.info else float("nan")
            self.result_label.setText(
                f"<b>Cd = {cd:.3f}</b> &nbsp; Cl = {cl:.3f} &nbsp; (Mittel letzte 20 %) &nbsp; "
                f"Cd*A = {cd * area:.3f} m2")
        else:
            self.result_label.setText("Keine Beiwerte gefunden.")

    def open_case_folder(self) -> None:
        d = self.case_dir or self.case_root
        d.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))

    def closeEvent(self, e) -> None:
        if self._runner.running:
            if QMessageBox.question(self, "Beenden", "Simulation laeuft noch. Abbrechen und beenden?") \
                    != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
            self._runner.cancel()
        self.monitor.stop()
        self.viewer.close()
        super().closeEvent(e)


def run(argv: list[str]) -> int:
    app = QApplication(argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    for a in argv[1:]:
        if os.path.isfile(a) and a.lower().endswith(SUPPORTED_EXTENSIONS):
            win.open_file(a)
            break
    return app.exec()


if __name__ == "__main__":
    sys.exit(run(sys.argv))
