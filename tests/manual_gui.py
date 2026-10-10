"""Manueller GUI-Test: laedt Geometrie und Ergebnisse und speichert einen Screenshot."""
import sys
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication

from windtunnel.ui.main_window import MainWindow

stl, png, results = sys.argv[1], sys.argv[2], Path(sys.argv[3])
app = QApplication([])
w = MainWindow()
w.resize(1500, 900)
w.show()
w.open_file(stl)


def pump(n):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.1)


pump(40)
w.speed.setValue(100)
w._load_results(results)
w.tabs.setCurrentIndex(0)
pump(30)
w.viewer.screenshot(png.replace(".png", "_3d.png"))
w.grab().save(png)
w.viewer.close()
