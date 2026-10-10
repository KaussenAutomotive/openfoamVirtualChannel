# Virtueller Windkanal (OpenFOAM)

Windows-App, die OpenFOAM (Foundation `dev`, in WSL) als virtuellen Windkanal fuer Fahrzeuge nutzbar macht:
Geometrie laden, Parameter waehlen, Simulation starten, Cd/Cl, Oberflaechendruck, Schnitte und Stromlinien ansehen.

## Funktionen

- Import: **STL** (binaer/ASCII), **OBJ**, **STEP/STP** (ueber Gmsh/OpenCASCADE), **3MF** (inkl. Komponenten, Transformationen, Einheiten). Auch per Drag & Drop.
- Einheiten-Erkennung (mm/cm/m/Zoll), Drehung um X/Y/Z, automatisches Aufstellen auf den Boden, optionaler Bodenabstand.
- Pruefung auf Wasserdichtigkeit, Berechnung der Stirnflaeche (Aref) und der Blockierung.
- Automatische Case-Erzeugung: `blockMesh` + `snappyHexMesh` (Verfeinerungsboxen, Nachlauf), `foamRun` (`incompressibleFluid`, RANS Spalart-Allmaras, stationaer), Rollende Strasse oder haftender Boden.
- Qualitaetsstufen Entwurf / Standard / Fein mit Zellzahl-Schaetzung, parallele Ausfuehrung (MPI).
- Live-Protokoll, Fortschritt und Residuen-Plot.
- Laufzeitzaehler mit Restzeitschaetzung (aus der Iterationsgeschwindigkeit von `foamRun`; Obergrenze, da die Konvergenzpruefung frueher beenden kann).
- Live-Auslastung: Gesamt-CPU, Auslastung je CPU-Kern und RAM der WSL-VM (Tab *Auslastung*, Kurzanzeige in der Statusleiste); startet automatisch mit der Simulation.
- Wind- und Wasserkanal: Medium *Luft* oder *Wasser* (kinematische Viskositaet 1,5e-5 bzw. 1,0e-6 m2/s), Reynolds-Zahl-Anzeige. Der Wasserkanal rechnet einphasig mit starrem Deckel, eine freie Oberflaeche (Wellen) wird nicht modelliert.
- Wasserlinie: Im Wasserkanal legt *Wasserlinie ueber Kiel* (Feld und Schieber) fest, wie tief der Rumpf eintaucht. Die Wasserlinie wird im 3D-Viewer als Wasserebene mit Schnittlinie am Rumpf gezeigt (eingetauchter Teil bluelich eingefaerbt). Ist der Wert kleiner als die Rumpfhoehe, wird der Rumpf an der Wasserlinie geschnitten (Doppelkoerper-Modell, Deckel = Symmetrieebene); ist er groesser, ist der Rumpf voll getaucht (U-Boot, Tauchtiefe und Verhaeltnis zur Rumpfhoehe werden angezeigt, Warnung bei geringer Tauchtiefe). Der Abstand Kiel - Kanalboden wird ueber *Bodenabstand* eingestellt. Stirnflaeche und Beiwerte beziehen sich auf den eingetauchten Teil.
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
