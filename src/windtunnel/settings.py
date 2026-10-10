from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Quality:
    base_div: int  # Basiszelle dx = L / base_div
    surf_min: int  # Verfeinerungslevel Oberflaeche (min, max)
    surf_max: int
    near_level: int  # Level in der Box um das Fahrzeug
    wake_level: int  # Level im Nachlauf
    max_cells: int
    iterations: int


QUALITY_PRESETS: dict[str, Quality] = {
    "Entwurf": Quality(4, 4, 5, 2, 1, 3_000_000, 600),
    "Standard": Quality(4, 5, 6, 2, 1, 12_000_000, 1200),
    "Fein": Quality(4, 6, 7, 3, 2, 40_000_000, 2000),
}


# Dichte (kg/m3), Erdbeschleunigung (m/s2)
RHO_WATER = 998.8
RHO_AIR = 1.0
NU_AIR = 1.48e-5
G = 9.81
# Kinematische Viskositaet bei 20 Grad C in m2/s
MEDIA: dict[str, float] = {"Luft": 1.5e-5, "Wasser": 1.0e-6}


@dataclass
class WslConfig:
    distro: str = "Ubuntu-22.04"
    bashrc: str = "/opt/openfoam-dev/etc/bashrc"
    work_dir: str = "$HOME/windtunnel"


@dataclass
class TunnelSettings:
    speed_kmh: float = 100.0
    rolling_road: bool = True
    quality: str = "Entwurf"
    cores: int = 8
    iterations: int = 0  # 0 = Voreinstellung der Qualitaetsstufe
    upstream: float = 3.0  # in Fahrzeuglaengen
    downstream: float = 6.0  # in Fahrzeuglaengen
    side: float = 3.0  # Halbbreite des Kanals in Fahrzeugbreiten
    top: float = 4.0  # Kanalhoehe in Fahrzeughoehen
    streamline_seeds: int = 9
    medium: str = "Luft"  # "Luft" = Windkanal, "Wasser" = Wasserkanal
    draft: float = 1.0  # Wasserkanal: Wasserlinie ueber dem Kiel in m (> Rumpfhoehe = voll getaucht)
    free_surface: bool = False  # Wasserkanal: Zweiphasen-VoF mit Wellen statt starrem Deckel
    case_name: str = "fahrzeug"
    wsl: WslConfig = field(default_factory=WslConfig)

    @property
    def is_water(self) -> bool:
        return self.medium == "Wasser"

    @property
    def is_vof(self) -> bool:
        return self.is_water and self.free_surface

    @property
    def nu(self) -> float:
        return MEDIA[self.medium]

    @property
    def speed_ms(self) -> float:
        return self.speed_kmh / 3.6

    @property
    def preset(self) -> Quality:
        return QUALITY_PRESETS[self.quality]

    @property
    def n_iterations(self) -> int:
        # VoF mit lokaler Zeitschrittsteuerung braucht deutlich mehr Iterationen
        return self.iterations or self.preset.iterations * (2 if self.is_vof else 1)
