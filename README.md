# Virtueller Windkanal (OpenFOAM)

Windows-App, die OpenFOAM (Foundation `dev`, in WSL) als virtuellen Windkanal fuer Fahrzeuge nutzbar macht:
Geometrie laden, Parameter waehlen, Simulation starten, Cd/Cl, Oberflaechendruck, Schnitte und Stromlinien ansehen.

## Funktionen

- Import: **STL** (binaer/ASCII), **BIN** (Binary-STL oder rohe float32-Dreiecksliste), **STEP/STP** (ueber Gmsh/OpenCASCADE), **3MF** (inkl. Komponenten, Transformationen, Einheiten). Auch per Drag & Drop.
- Einheiten-Erkennung (mm/cm/m/Zoll), Drehung um X/Y/Z, automatisches Aufstellen auf den Boden, optionaler Bodenabstand.
- Pruefung auf Wasserdichtigkeit, Berechnung der Stirnflaeche (Aref) und der Blockierung.
- Automatische Case-Erzeugung: `blockMesh` + `snappyHexMesh` (Verfeinerungsboxen, Nachlauf), `foamRun` (`incompressibleFluid`, RANS Spalart-Allmaras, stationaer), Rollende Strasse oder haftender Boden.
- Qualitaetsstufen Entwurf / Standard / Fein mit Zellzahl-Schaetzung, parallele Ausfuehrung (MPI).
- Live-Protokoll, Fortschritt und Residuen-Plot.
- Ergebnisse: Cd, Cl, Cd*A, Beiwert-Verlauf, Oberflaechen-cp, Geschwindigkeits-Schnitte (y/z) und Stromlinien im 3D-Viewer.
- Stromlinien-Darstellung: duenne, nach Geschwindigkeit eingefaerbte Roehren (blau = langsam, rot = schnell), Dicke, Dichte und Ausschnitt skalieren mit der Fahrzeuglaenge; das CAD-Modell bleibt sichtbar, die Kamera rahmt das Fahrzeug ein (Iso/Seite/Front/Oben).

## Voraussetzungen

- Python 3.10+ (getestet mit 3.14) unter Windows
- WSL2 mit OpenFOAM Foundation `dev` (Standard: Distribution `Ubuntu-22.04`, `/opt/openfoam-dev/etc/bashrc`); anpassbar unter *Einstellungen*

## Start

```
run.bat                       # legt beim ersten Start .venv an und installiert requirements.txt
run.bat pfad\zum\fahrzeug.stl # optional mit Geometrie
```

Tests: `.venv\Scripts\python -m pytest`

## Ablauf

1. Geometrie laden (Einheit pruefen, Fahrzeug so drehen, dass die Front nach -x zeigt; Anstroemung ist +x, z ist oben).
2. Geschwindigkeit, Boden, Kanalabmessungen und Qualitaet einstellen.
3. *Fall erzeugen und rechnen*. Der Fall liegt unter `%USERPROFILE%\WindTunnelCases\<name>`, gerechnet wird im WSL-Dateisystem (`$HOME/windtunnel/<name>`), Ergebnisse werden nach `results/` zurueckkopiert.
4. Ergebnisse erscheinen automatisch im 3D-Viewer; *Ergebnisse laden ...* oeffnet einen vorhandenen Fall.

## Hinweise

- Die Qualitaetsstufe "Entwurf" ist fuer Funktionstests gedacht; fuer belastbare Cd-Werte "Standard" oder "Fein" und genuegend Iterationen verwenden.
- Offene Netze (nicht wasserdicht) fuehren haeufig zu fehlerhaften `snappyHexMesh`-Ergebnissen.
- Nicht enthalten: Grenzschichtzellen, Raddrehung (MRF), Feature-Kanten-Verfeinerung, transiente/LES-Simulation.
