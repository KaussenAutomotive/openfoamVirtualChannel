# TODO / Übergabe für Folge-Chats – OpenFOAM Virtual Windtunnel (Windows-App)

## Ziel
Windows-App zum einfachen Einbinden von OpenFOAM in einen grafischen virtuellen Windkanal für Fahrzeuge.
Geometrie-Import: **STL, STEP, OBJ, 3MF** (das urspruenglich genannte "BIN" war ein Tippfehler fuer OBJ). Der Workspace ist aktuell leer (noch nichts implementiert).

## Verifizierte Umgebung (Stand 2026-10-10)
- Windows, Python 3.14.x (`py -0p`: `C:\Users\thoma\AppData\Local\Python\pythoncore-3.14-64\python.exe`), pip 26.2
- **Kein .NET SDK**, Node v26 vorhanden -> Entscheidung: Python-App
- `vtk` 9.7.1 hat ein cp314-win_amd64-Wheel (Download getestet)
- WSL2, Distro `Ubuntu-22.04`, 16 Kerne, ~30 GB RAM
- OpenFOAM: **OpenFOAM Foundation `dev`** (openfoam.org), kompiliert in `/opt/openfoam-dev`
  - Env: `source /opt/openfoam-dev/etc/bashrc`, Plattform `linux64GccDPInt32Opt`
  - Windows-Zugriff: `\\wsl$\Ubuntu-22.04\opt\openfoam-dev\...`
  - Referenzfall: `tutorials/incompressibleFluid/motorBike/motorBike`
- Terminal-Hinweis: komplexe Bash-Befehle nicht inline über `wsl -- bash -lc "..."` quoten (PowerShell zerlegt sie); stattdessen Skriptdatei oder UNC-Pfad nutzen. `grep`/`sed` gibt es nicht in PowerShell.

## Technik-Entscheidungen
- GUI: PySide6; 3D-Viewer: pyvista + pyvistaqt (VTK)
- STL/OBJ: pyvista-Reader (STL binaer ueber Dateigroesse `84 + 50*n` erkannt)
- 3MF: eigener Reader (zipfile + xml.etree; Build-Items, Components, Transformationen, Einheiten) – keine trimesh/lxml-Abhängigkeit
- STEP: `gmsh` (pip, OpenCASCADE) -> Oberflächennetz -> STL
- Einheiten-Auswahl (mm/m) mit Auto-Erkennung aus der Bounding Box (Länge > 20 -> mm)
- OpenFOAM-Ausführung: `wsl.exe -d Ubuntu-22.04 -- bash -lc "source /opt/openfoam-dev/etc/bashrc && ..."` über `QProcess`, Live-Log; WSL-Distro und bashrc-Pfad in den Einstellungen konfigurierbar
- Koordinaten: x = Anströmung, z = oben, y = quer; Fahrzeug auf z = 0 (Boden) ausrichten
- Distribution später: PyInstaller (`run.bat` + Build-Skript)

## Geplante Struktur
```
pyproject.toml / requirements.txt
src/windtunnel/
  __main__.py
  settings.py        # Dataclasses: Geschwindigkeit, Qualität, Domain, Boden, WSL-Config
  importers.py       # stl/obj/step/3mf -> pyvista.PolyData
  geometry.py        # Skalierung, Rotation, Bodenausrichtung, STL-Export, Bounding Box, Stirnfläche
  case_builder.py    # OpenFOAM-Case erzeugen
  runner.py          # WSL/QProcess-Pipeline
  results.py         # forceCoeffs/Residuen parsen, VTK-Ergebnisse laden
  ui/main_window.py, ui/viewer.py, ui/panels.py
```

## OpenFOAM-Dev-Syntax (aus motorBike-Tutorial verifiziert)
- `controlDict`: `solver incompressibleFluid;` (Solver-Start per `foamRun`, **nicht** `simpleFoam`)
- Turbulenz: `constant/momentumTransport` (`simulationType RAS; RAS { model SpalartAllmaras; turbulence on; }`), Viskosität: `constant/physicalProperties` (`viscosityModel constant; nu 1.5e-05;`)
- `fvSolution`: `SIMPLE { nNonOrthogonalCorrectors 0; }`, `potentialFlow`-Block, `Phi`-Solver, `relaxationFactors { p 0.3; U 0.5; nuTilda 0.5; }`
- Wandfunktion: `nutUSpaldingWallFunction`; Auslass `inletOutlet` für U/nuTilda; `proc.*` -> `processor`
- `system/functions` wird automatisch gelesen; darin `#includeFunc cutPlane` / `streamlines` / `forceCoeffs`, die jeweils als Datei in `system/` liegen
- `system/forceCoeffs`: `type forceCoeffs; libs ("libforces.so"); patches (...); rho rhoInf; rhoInf 1; liftDir; dragDir; CofR; pitchAxis; magUInf; lRef; Aref;`
- `system/cutPlane`: `type surfaces; libs ("libsampling.so"); surfaceFormat vtk; fields (p U); surfaces { yNormal { type cutPlane; ... interpolate true; } }`
- `system/streamlines`: `type streamlines; libs ("libfieldFunctionObjects.so"); seedSampleSet { type lineUniform; start; end; nPoints; } ...` (Vorlagen auch in `etc/caseDicts/functions/streamlines/`)
- Ablauf (motorBike-Allrun): `blockMesh` -> `decomposePar -copyZero` -> `snappyHexMesh -parallel -overwrite` -> `renumberMesh -parallel -overwrite` -> `potentialFoam -initialiseUBCs -parallel` -> `foamRun -parallel` -> `reconstructPar -latestTime`
- Verfügbare Binaries u. a.: `blockMesh`, `snappyHexMesh`, `surfaceFeatures`, `surfaceCheck`, `surfaceTransformPoints`, `checkMesh`, `decomposePar`, `reconstructPar`, `renumberMesh`, `foamPostProcess`, `foamToVTK` (kann `-parallel`)
- Boundary-Patches der Domain: `inlet`, `outlet`, `lowerWall` (Rolling Road = `fixedValue` mit Windgeschwindigkeit, sonst Slip/Haftung), `upperWall`, `front`, `back` (Symmetrie)
- Patches des Fahrzeugs heißen `<stlname>_<region>` -> Regex wie `"vehicle.*"` nutzen

## Auslegung Mesh/Domain (Entwurf)
- Domain: stromauf ~3 L, stromab ~6 L, seitlich/oben ~3-4 Fahrzeugmaße; Blockierungsgrad anzeigen
- Basiszellgröße dx = L/8; Qualitätsstufen (Oberflächen-Level min/max): Entwurf (3,4), Standard (4,5), Fein (5,6)
- `maxGlobalCells`: ca. 4 Mio / 15 Mio / 40 Mio; `maxLocalCells` = global / Prozessanzahl
- Verfeinerungsboxen um das Fahrzeug (Level-1) und im Nachlauf (Level-2)
- `insidePoint` im Luftvolumen (nicht im Fahrzeug) wählen, z. B. stromauf vor dem Fahrzeug
- Referenzgrößen: `lRef` = Fahrzeuglänge, `Aref` = projizierte Stirnfläche (aus Mesh berechnen), `CofR` = Fahrzeugmitte am Boden
- `decomposeParDict`: `numberOfSubdomains` = Kernanzahl (z. B. 8-16), hierarchical/scotch

## Ergebnis-Visualisierung
- `foamToVTK -parallel -latestTime -noInternal -patches '(vehicle.*)'` -> Oberflächendruck (cp) auf dem Fahrzeug
- Schnittebene: `postProcessing/cutPlane/<zeit>/*.vtk`; Stromlinien: `postProcessing/streamlines/<zeit>/*.vtk`
  - Ausgabedateinamen zur Laufzeit per Glob finden (Namen noch nicht verifiziert)
  - Mehrere Seed-Linien programmatisch erzeugen (jeweils eigene Function-Datei oder `points`-Seed), `lineUniform` nimmt nur eine Linie
- Cd/Cl/Cm-Verlauf aus `postProcessing/forceCoeffs/0/coefficient.dat`, Residuen aus dem Solver-Log live plotten

## Status
- [x] Punkte 1-10 und 12 umgesetzt (Importer, Geometrie, Case-Builder, WSL-Runner, Ergebnisse, GUI, Tests, README); End-to-End-Lauf in WSL verifiziert (Entwurf, 150 Iterationen).
- [x] Verifiziert: foamToVTK nutzt -excludePatches (kein -patches); Vehicle-Patch heisst 'vehicle'; Ausgabedateien: forceCoeffs/0/forceCoeffs.dat, streamlinesN/<t>/tracks.vtk, cutPlane/<t>/yNormal.vtk; VTK-Links mit cp -rL kopieren.
- [x] Laufzeitzaehler mit Restzeitschaetzung (timing.py), Live-CPU/RAM-Auslastung der WSL-VM (monitor.py, Tab "Auslastung"), Wasserkanal (Medium Wasser, nu = 1e-6, starrer Deckel ohne freie Oberflaeche).
- [x] Wasserlinie/Eintauchtiefe im Wasserkanal (Doppelkoerper-Modell mit starrem Deckel auf Wasserlinie; teilweise und voll getaucht, getestet in WSL).
- [ ] Offen: Freie Oberflaeche mit VoF (foamRun incompressibleVoF, alpha.water, Wellenwiderstand) fuer Ueberwasserschiffe; Halbmodell/Symmetrie, Raddrehung (MRF), Grenzschichtzellen, Feature-Kanten (surfaceFeatures), PyInstaller-Build, Projektdatei speichern/laden, Live-Cd waehrend des Laufs, Kalibrierung der Zellzahl-Schaetzung an Standard/Fein, GUI-Test mit STEP/3MF-Dateien aus echten CAD-Programmen.

## Offene Fragen an den Nutzer
- ~~Ist `.bin` ein Binary-STL~~ Geklaert: gemeint war OBJ.
- Soll die App auch Docker/native Windows-OpenFOAM unterstützen oder nur WSL?
- Stationärer RANS (Standard, Annahme) oder auch transient/LES?

