"""Manueller Test des WslRunner gegen einen mit manual_case.py erzeugten Fall."""
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from windtunnel.runner import WslRunner, check_wsl
from windtunnel.settings import TunnelSettings

case = Path(sys.argv[1])
s = TunnelSettings(cores=8, case_name="testcar", quality="Entwurf", iterations=150)
print(check_wsl(s.wsl))
app = QCoreApplication([])
r = WslRunner()
r.step.connect(lambda n, i, t: print(f"STEP {i}/{t} {n}"))
r.progress.connect(lambda i, t: print("iter", i, t) if i % 50 == 0 else None)
r.finished.connect(lambda ok, m: (print("FINISHED", ok, m), app.quit()))
r.start(case / "run_wsl.sh", s)
app.exec()
