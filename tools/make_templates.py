#!/usr/bin/env python3
"""Generate the built-in template dashboards in web/layouts/.

    python3 tools/make_templates.py

Templates are ordinary read-only dashboards (use "Save as" in the editor to make your own copy). Each carries BOTH the
flat-look properties (text labels, colours) and the skinned-look ones (icons, sweeps, labels), so it works in every
look: a look only reads what it needs. Edit the arrangements here and re-run, rather than hand-editing the JSON.

    cluster    an instrument cluster: big speedo and tach, four small gauges, gear strip, warning lamps
    offroad    the cluster with driveline lamps (4WD, low range, diff locks) for trucks
    minimal    a big speed readout, gear, an rpm bar and a few lamps: nothing to distract you
    phone      a portrait layout for a phone on the network: big speed, a tach, gear, three bars, lamps
    health     tyres, body damage, brake temperatures and engine-state warning lamps
    drift      a big tach with shift light, gear digit, speed, throttle/brake bars and a throttle history
    telemetry  readouts plus six history graphs, for tuning and testing
    navigation the live map, the route panel with its next turn, the speed limit sign and your speed (needs BeamNG.Earth)
    trackday   the lap timer, a tach with shift light, G-force, speed, gear and pedal bars (laps need BeamNG.Earth and a circuit)
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "web" / "layouts"


class Builder:
    """Adds widgets with stable, readable ids ("roundGauge-1", "light-3"...)."""

    def __init__(self):
        self.widgets = []
        self._n = {}

    def add(self, wtype, /, **props):  # positional-only: widgets also have a "kind" (unit type) property
        self._n[wtype] = self._n.get(wtype, 0) + 1
        self.widgets.append({"id": f"{wtype}-{self._n[wtype]}", "type": wtype, **props})


# (channel, skinned symbol, lamp colour, flat-look text or arrow, flat-look colour)
LAMP = {
    "left": ("signal_L", "left", "green", {"arrow": "left"}, "var(--ok)"),
    "right": ("signal_R", "right", "green", {"arrow": "right"}, "var(--ok)"),
    "high": ("highbeam", "highbeam", "blue", {"text": "HI"}, "var(--accent)"),
    "low": ("lowbeam", "lowbeam", "green", {"text": "LO"}, "var(--ok)"),
    "engine": ("checkengine", "engine", "amber", {"text": "ENG"}, "var(--warn)"),
    "oil": ("oil", "oil", "red", {"text": "OIL"}, "var(--crit)"),
    "fuel": ("lowfuel", "fuel", "amber", {"text": "FUEL"}, "var(--warn)"),
    "abs": ("absActive", "abs", "amber", {"text": "ABS"}, "var(--warn)"),
    "park": ("parkingbrake", "parking", "red", {"text": "P"}, "var(--crit)"),
    "hazard": ("hazard", "hazard", "red", {"text": "HAZ"}, "var(--crit)"),
    "esc": ("escActive", "esc", "amber", {"text": "ESC"}, "var(--warn)"),
    # driveline (trucks): the channels are on/off, and only present on vehicles that have them
    "four": ("mode4WD", None, "blue", {"text": "4WD"}, "var(--accent)"),
    "range": ("rangebox_state", None, "amber", {"text": "LOW"}, "var(--warn)"),
    "fdiff": ("front_lock", None, "amber", {"text": "FDIFF"}, "var(--warn)"),
    "rdiff": ("rear_lock", None, "amber", {"text": "RDIFF"}, "var(--warn)"),
    "cdiff": ("transfercase_lock", None, "amber", {"text": "CTR"}, "var(--warn)"),
}


def lamp_row(b, keys, center_x, y, size=48, gap=8):
    x0 = center_x - (len(keys) * size + (len(keys) - 1) * gap) // 2
    for i, key in enumerate(keys):
        channel, icon, colour, flat, flat_colour = LAMP[key]
        props = {"channel": channel, "lampColor": colour, "color": flat_colour, **flat}
        if icon:
            props["icon"] = icon
        b.add("light", x=x0 + i * (size + gap), y=y, w=size, h=size, **props)


def layout(name, w, h, widgets):
    return {"format": 1, "name": name, "width": w, "height": h, "scaleMode": "max", "scalePercent": 100, "widgets": widgets}


# ---- shared dial definitions -------------------------------------------------------------------------------------

SPEED = dict(channel="wheelspeed", kind="speed", label="SPEED", min=0, max={"kmh": 240, "mph": 150},
             majorStep={"kmh": 40, "mph": 20}, minorDiv=4, redFrom=0.92)
TACH = dict(channel="rpm", label="RPM", showUnit="x1000", min=0, maxChannel="maxrpm", maxRound=1000, majorStep=1000,
            minorDiv=2, labelDiv=1000, redFrom=0.85)


def small_dials(b, x, y, size, load_label="LOAD"):
    """Water, oil, fuel and load in a 2x2 block."""
    b.add("roundGauge", x=x, y=y, w=size, h=size, channel="watertemp", kind="temp", label="WATER", compact=True, icon="temp",
          min={"c": 40, "f": 100}, max={"c": 130, "f": 270}, majorStep={"c": 30, "f": 60}, minorDiv=3,
          startAngle=-105, endAngle=105, coldTo=0.12, redFrom=0.85)
    b.add("roundGauge", x=x + size + 4, y=y, w=size, h=size, channel="oiltemp", kind="temp", label="OIL", compact=True, icon="oil",
          min={"c": 40, "f": 100}, max={"c": 150, "f": 300}, majorStep={"c": 55, "f": 100}, minorDiv=5,
          startAngle=-105, endAngle=105, redFrom=0.85)
    b.add("roundGauge", x=x, y=y + size + 5, w=size, h=size, channel="fuel", kind="fraction", label="FUEL", showUnit="", compact=True,
          icon="fuel", min=0, max=100, majorStep=50, minorDiv=4, labels=["E", "½", "F"], startAngle=-62, endAngle=62, lowTo=0.12)
    b.add("roundGauge", x=x + size + 4, y=y + size + 5, w=size, h=size, channel="engineLoad", kind="fraction", label=load_label,
          compact=True, min=0, max=100, majorStep=50, minorDiv=5, startAngle=-105, endAngle=105, redFrom=0.9)


def cluster_like(name, lamps):
    b = Builder()
    b.add("roundGauge", x=20, y=30, w=480, h=480, **SPEED, sub="odometer", subChannel="odometer", subKind="distance")
    b.add("roundGauge", x=1000, y=30, w=480, h=480, **TACH, shiftFrom=0.9)
    small_dials(b, 528, 40, 220)
    b.add("gear", x=585, y=498, w=330, h=48, channel="gear", label="", style="strip")
    lamp_row(b, lamps, 750, 560)
    return layout(name, 1500, 620, b.widgets)


# ---- the templates -----------------------------------------------------------------------------------------------

def cluster():
    return cluster_like("Instrument cluster", ["left", "right", "high", "low", "engine", "oil", "fuel", "abs", "park", "hazard", "esc"])


def offroad():
    return cluster_like("Off-road / truck", ["left", "right", "four", "range", "fdiff", "rdiff", "cdiff", "engine", "oil", "fuel", "park"])


def minimal():
    b = Builder()
    b.add("readout", x=30, y=30, w=620, h=240, channel="wheelspeed", kind="speed", label="SPEED", decimals=0, digits=3)
    b.add("gear", x=700, y=30, w=220, h=240, channel="gear", label="", style="digit")
    b.add("readout", x=950, y=30, w=220, h=110, channel="fuel", kind="fraction", label="FUEL", decimals=0, digits=3)
    b.add("readout", x=950, y=160, w=220, h=110, channel="watertemp", kind="temp", label="WATER", decimals=0, digits=3)
    b.add("bar", x=30, y=300, w=1140, h=50, channel="rpm", label="RPM", min=0, max=8000, maxChannel="maxrpm", maxRound=1000,
          warnFrom=0.8, critFrom=0.92, segments=40)
    lamp_row(b, ["left", "right", "engine", "oil", "abs", "park"], 600, 368, size=44)
    return layout("Minimal", 1200, 420, b.widgets)


def phone():
    b = Builder()
    b.add("readout", x=20, y=20, w=500, h=220, channel="wheelspeed", kind="speed", label="SPEED", decimals=0, digits=3)
    b.add("roundGauge", x=70, y=255, w=400, h=400, **TACH, shiftFrom=0.9)
    b.add("gear", x=20, y=670, w=170, h=150, channel="gear", label="", style="digit")
    b.add("bar", x=205, y=670, w=315, h=40, channel="fuel", kind="fraction", label="FUEL", min=0, max=100, invert=True, warnFrom=20, critFrom=8, segments=20)
    b.add("bar", x=205, y=722, w=315, h=40, channel="watertemp", kind="temp", label="WATER", min={"c": 40, "f": 100}, max={"c": 130, "f": 270},
          warnFrom={"c": 105, "f": 220}, critFrom={"c": 115, "f": 240}, segments=20)
    b.add("bar", x=205, y=774, w=315, h=40, channel="oiltemp", kind="temp", label="OIL", min={"c": 40, "f": 100}, max={"c": 150, "f": 300},
          warnFrom={"c": 125, "f": 260}, critFrom={"c": 140, "f": 285}, segments=20)
    lamp_row(b, ["left", "right", "engine", "oil", "abs", "park"], 270, 845, size=56, gap=10)
    return layout("Phone (portrait)", 540, 930, b.widgets)


# (channel, flat-look text, lamp colour): the game's engine component states, worst first
ENGINE_STATES = [
    ("state.engine.coolantOverheating", "COOLNT", "red"), ("state.engine.oilOverheating", "OIL HT", "red"),
    ("state.engine.oilLevelCritical", "OIL LO", "red"), ("state.engine.starvedOfOil", "STARVD", "red"),
    ("state.engine.headGasketDamaged", "GASKET", "red"), ("state.engine.engineHydrolocked", "HYDRO", "red"),
    ("state.engine.engineLockedUp", "LOCKED", "red"), ("state.engine.overRevDanger", "OVRREV", "amber"),
    ("state.engine.overTorqueDanger", "OVRTRQ", "amber"), ("state.engine.turbochargerHot", "TURBO", "amber"),
    ("state.engine.radiatorLeak", "RAD LK", "amber"), ("state.engine.engineReducedTorque", "LIMP", "amber"),
]


def health():
    b = Builder()
    b.add("tyres", x=20, y=20, w=300, h=400, source="temp", showPressure=True)
    b.add("damage", x=340, y=20, w=240, h=400, label="Body damage", showPercent=True, showTyres=True)
    b.add("readout", x=610, y=20, w=245, h=104, channel="damage.deformedPct", label="DEFORMED", showUnit="%", decimals=1, digits=5)
    b.add("readout", x=870, y=20, w=245, h=104, channel="damage.brokenPct", label="BROKEN", showUnit="%", decimals=1, digits=5)
    for i, w in enumerate(("FL", "FR", "RL", "RR")):
        b.add("bar", x=610, y=142 + i * 44, w=505, h=34, channel=f"wheelThermals.{w}.brakeSurfaceTemperature", kind="temp", label=f"BRK {w}",
              min={"c": 0, "f": 32}, max={"c": 900, "f": 1650}, warnFrom={"c": 500, "f": 932}, critFrom={"c": 700, "f": 1292}, segments=30)
    for i, (channel, text, colour) in enumerate(ENGINE_STATES):
        col, row = i % 6, i // 6
        b.add("light", x=610 + col * 86, y=336 + row * 52, w=80, h=44, channel=channel, text=text, lampColor=colour,
              color="var(--crit)" if colour == "red" else "var(--warn)")
    return layout("Vehicle health", 1140, 440, b.widgets)


def driver():
    """What the driver and the car are doing: controls, G-force, and the basics (for driving practice, or a wheel setup)."""
    b = Builder()
    b.add("inputs", x=20, y=20, w=420, h=330, label="Driver inputs", showWheel=True, showClutch=False)
    b.add("gmeter", x=460, y=20, w=330, h=400, label="G-force", range=1.5, showTrail=True)
    b.add("readout", x=810, y=20, w=300, h=130, channel="wheelspeed", kind="speed", label="SPEED", decimals=0, digits=3)
    b.add("gear", x=810, y=170, w=300, h=170, channel="gear", label="", style="digit")
    b.add("bar", x=810, y=360, w=300, h=44, channel="rpm", label="RPM", min=0, max=8000, maxChannel="maxrpm", maxRound=1000,
          warnFrom=0.8, critFrom=0.92, segments=30)
    lamp_row(b, ["left", "right", "abs", "esc", "park"], 230, 378, size=40)
    return layout("Driver inputs and G-force", 1130, 440, b.widgets)


def trip():
    """Both trip meters side by side with the essentials, for a long drive."""
    b = Builder()
    b.add("trip", x=20, y=20, w=300, h=360, trip="A", label="Trip A")
    b.add("trip", x=340, y=20, w=300, h=360, trip="B", label="Trip B")
    b.add("readout", x=660, y=20, w=320, h=130, channel="wheelspeed", kind="speed", label="SPEED", decimals=0, digits=3)
    b.add("gear", x=660, y=165, w=320, h=130, channel="gear", label="", style="digit")
    b.add("bar", x=660, y=315, w=320, h=30, channel="fuel", kind="fraction", label="FUEL", min=0, max=100, invert=True, warnFrom=20, critFrom=8, segments=20)
    b.add("bar", x=660, y=355, w=320, h=30, channel="watertemp", kind="temp", label="WATER", min={"c": 40, "f": 100}, max={"c": 130, "f": 270},
          warnFrom={"c": 105, "f": 220}, critFrom={"c": 115, "f": 240}, segments=20)
    return layout("Trip computer", 1000, 400, b.widgets)


def drift():
    b = Builder()
    b.add("roundGauge", x=480, y=20, w=440, h=440, **TACH, shiftFrom=0.88)
    b.add("readout", x=40, y=40, w=400, h=170, channel="wheelspeed", kind="speed", label="SPEED", decimals=0, digits=3)
    b.add("bar", x=40, y=235, w=400, h=36, channel="throttle", kind="fraction", label="THR", min=0, max=100, segments=24)
    b.add("bar", x=40, y=283, w=400, h=36, channel="brake", kind="fraction", label="BRK", min=0, max=100, segments=24)
    b.add("graph", x=40, y=345, w=400, h=150, channel="throttle", kind="fraction", label="Throttle", min=0, max=100, seconds=15)
    b.add("gear", x=960, y=30, w=400, h=260, channel="gear", label="", style="digit")
    b.add("bar", x=960, y=325, w=400, h=34, channel="watertemp", kind="temp", label="WATER", min={"c": 40, "f": 100}, max={"c": 130, "f": 270},
          warnFrom={"c": 105, "f": 220}, critFrom={"c": 115, "f": 240}, segments=24)
    b.add("bar", x=960, y=375, w=400, h=34, channel="oiltemp", kind="temp", label="OIL", min={"c": 40, "f": 100}, max={"c": 150, "f": 300},
          warnFrom={"c": 125, "f": 260}, critFrom={"c": 140, "f": 285}, segments=24)
    lamp_row(b, ["engine", "oil", "abs", "esc", "park", "left", "right"], 700, 492)
    return layout("Drift / track", 1400, 560, b.widgets)


def telemetry():
    b = Builder()
    # a row of numbers across the top...
    b.add("readout", x=20, y=20, w=330, h=100, channel="wheelspeed", kind="speed", label="SPEED", decimals=0, digits=3)
    b.add("readout", x=365, y=20, w=330, h=100, channel="rpm", label="RPM", decimals=0, digits=5)
    b.add("gear", x=710, y=20, w=200, h=100, channel="gear", label="", style="digit")
    b.add("readout", x=925, y=20, w=215, h=100, channel="fuel", kind="fraction", label="FUEL", decimals=0, digits=3)
    b.add("readout", x=1155, y=20, w=225, h=100, channel="watertemp", kind="temp", label="WATER", decimals=0, digits=3)
    # ...and six history graphs below
    g = dict(seconds=30)
    b.add("graph", x=20, y=140, w=445, h=260, channel="wheelspeed", kind="speed", label="Speed", min=0, max={"kmh": 240, "mph": 150}, **g)
    b.add("graph", x=478, y=140, w=445, h=260, channel="rpm", label="RPM", min=0, max=8000, **g)
    b.add("graph", x=935, y=140, w=445, h=260, channel="throttle", kind="fraction", label="Throttle", min=0, max=100, **g)
    b.add("graph", x=20, y=420, w=445, h=260, channel="brake", kind="fraction", label="Brake", min=0, max=100, **g)
    b.add("graph", x=478, y=420, w=445, h=260, channel="watertemp", kind="temp", label="Water", min={"c": 40, "f": 100}, max={"c": 130, "f": 270}, **g)
    b.add("graph", x=935, y=420, w=445, h=260, channel="oiltemp", kind="temp", label="Oil", min={"c": 40, "f": 100}, max={"c": 150, "f": 300}, **g)
    return layout("Telemetry (graphs)", 1400, 700, b.widgets)


def navigation():
    """For a drive with a GPS route: the map, where to turn, how far is left, and the limit. Everything about the road comes from BeamNG.Earth."""
    b = Builder()
    b.add("tripmap", x=20, y=20, w=760, h=560, headUp=True, zoom=17, satellite=False)
    b.add("route", x=800, y=20, w=340, h=420, label="Route", showNext=True, showName=True, showOff=True)
    b.add("speedlimit", x=1160, y=20, w=220, h=270, speedChannel="wheelspeed", style="round", showSpeed=False)
    b.add("gear", x=1160, y=300, w=220, h=100, channel="gear", label="", style="digit")
    b.add("readout", x=800, y=460, w=340, h=120, channel="wheelspeed", kind="speed", label="SPEED", decimals=0, digits=3)
    b.add("readout", x=1160, y=410, w=220, h=104, channel="fuel", kind="fraction", label="FUEL", decimals=0, digits=3)
    lamp_row(b, ["left", "right", "engine", "park"], 1270, 528, size=46)
    return layout("Navigation", 1400, 600, b.widgets)


def trackday():
    """For timed laps: this lap and the gap to your best, large, with the revs, gear and what your feet and the car are doing."""
    b = Builder()
    b.add("laptimer", x=20, y=20, w=440, h=520, label="Lap timer", showDelta=True, showSectors=True)
    b.add("roundGauge", x=480, y=20, w=440, h=440, **TACH, shiftFrom=0.88)
    b.add("gear", x=940, y=20, w=200, h=250, channel="gear", label="", style="digit")
    b.add("gmeter", x=1160, y=20, w=220, h=250, label="G-force", range=1.5, showTrail=True)
    b.add("readout", x=940, y=290, w=440, h=150, channel="wheelspeed", kind="speed", label="SPEED", decimals=0, digits=3)
    b.add("bar", x=940, y=460, w=440, h=36, channel="throttle", kind="fraction", label="THR", min=0, max=100, segments=24)
    b.add("bar", x=940, y=504, w=440, h=36, channel="brake", kind="fraction", label="BRK", min=0, max=100, segments=24)
    lamp_row(b, ["engine", "oil", "abs", "esc", "park"], 700, 485)
    return layout("Track day (lap timing)", 1400, 560, b.widgets)


TEMPLATES = {"navigation": navigation, "trackday": trackday, "cluster": cluster, "offroad": offroad, "minimal": minimal, "phone": phone, "drift": drift, "telemetry": telemetry, "health": health, "driver": driver, "trip": trip}

if __name__ == "__main__":
    for file, build in TEMPLATES.items():
        data = build()
        (OUT / f"{file}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {file}.json  {data['name']!r}  {data['width']}x{data['height']}  {len(data['widgets'])} widgets")
