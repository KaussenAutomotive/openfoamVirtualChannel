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
    QProgressBar, QPushButton, QScrollArea, QSpinBox, QTabWidget, QToolBar, QVBoxLayout, QWidget,
)

from .. import __version__
from ..case_builder import blockage_ratio, build_case, compute_domain, estimate_cells
from ..geometry import (NATIVE_UNIT_LABEL, UNIT_CHOICES, Placement, VehicleInfo, analyze,
                        guess_unit_label, place_vehicle)
from ..importers import FILE_FILTER, SUPPORTED_EXTENSIONS, GeometryImportError, ImportResult, load_geometry
from ..results import FlowResults, load_results
from ..runner import STEPS, WslRunner, check_wsl
from ..settings import QUALITY_PRESETS, TunnelSettings, WslConfig
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
        self.setWindowTitle(f"Virtueller Windkanal {__version__}")
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
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().showMessage("Geometrie per Menue oder Drag & Drop laden (STL, BIN, STEP, 3MF).")

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
        g = QGroupBox("Windkanal")
        f = QFormLayout(g)
        self.speed = _dspin(5, 400, 100, 5, 0, " km/h")
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
        for w in (self.up, self.down, self.side, self.top):
            w.valueChanged.connect(lambda *_: self._update_estimates())
        return g

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
        self.stream_width = _dspin(0.3, 6, 1.5, 0.3, 1, "x")
        self.stream_width.valueChanged.connect(self._apply_display)
        self.stream_density = _dspin(5, 100, 40, 5, 0, " %")
        self.stream_density.valueChanged.connect(self._apply_display)
        f.addRow("Fahrzeug", self.mode)
        f.addRow("Stromlinien-Dicke", self.stream_width)
        f.addRow("Stromlinien-Dichte", self.stream_density)
        for c in (self.chk_domain, self.chk_slice_y, self.chk_slice_z, self.chk_streams):
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
        self.statusBar().showMessage(f"{res.source.name} geladen ({res.mesh.n_cells} Dreiecke).")

    def _update_vehicle(self, reset_camera: bool = False) -> None:
        if self.raw is None:
            return
        p = Placement(UNIT_CHOICES[self.unit.currentText()], *(w.value() for w in self.rot),
                      self.clearance.value() / 1000.0)
        self.vehicle = place_vehicle(self.raw, p)
        self.info = analyze(self.vehicle)
        i = self.info
        warn = "" if i.watertight else (
            f"<br><b style='color:#d9822b'>Netz nicht wasserdicht ({i.open_edges} offene Kanten) - "
            "snappyHexMesh kann fehlschlagen.</b>")
        self.info_label.setText(
            f"L x B x H: {i.length:.2f} x {i.width:.2f} x {i.height:.2f} m<br>"
            f"Stirnflaeche: {i.frontal_area:.3f} m2, Dreiecke: {i.n_triangles}{warn}")
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
        self.tunnel_info.setText(f"Blockierung: {100 * blockage_ratio(self.info, s):.1f} %")

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
        self.tabs.setCurrentIndex(1)
        self._append_log(f"Fall erzeugt: {ci.path}")
        self._runner.start(ci.run_script, s)

    def _on_step(self, name: str, idx: int, total: int) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(idx)
        self.progress.setFormat(f"{name} (%v/%m)")
        self.statusBar().showMessage(f"Schritt {idx}/{total}: {name}")

    def _on_progress(self, it: int, total: int) -> None:
        self.statusBar().showMessage(f"foamRun: Iteration {it}/{total}")

    def _append_log(self, line: str) -> None:
        self.log.appendPlainText(line)

    def _on_run_finished(self, ok: bool, msg: str) -> None:
        self.act_run.setEnabled(True)
        self.act_stop.setEnabled(False)
        self.progress.setVisible(False)
        self.statusBar().showMessage(msg)
        if ok and self.case_dir is not None:
            self._load_results(self.case_dir / "results")
            self.tabs.setCurrentIndex(0)
        elif not ok:
            self.tabs.setCurrentIndex(2)
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
            flow = load_results(results_dir, self.settings().speed_ms)
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
