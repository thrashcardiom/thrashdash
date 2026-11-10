# Phase 0 findings (BeamNG 0.40.5.0, Linux native)

Everything below was read from the installed game's own Lua (read-only) unless marked **UNVERIFIED**.
The game folder is `~/.local/share/Steam/steamapps/common/BeamNG.drive`; the user folder is
`~/.local/share/BeamNG/BeamNG.drive/current` (mods go in `mods/unpacked/<name>/` or `mods/<name>.zip`).

## Answers to the spike questions

| Question | Answer | Evidence |
|:---|:---|:---|
| Is a socket library available to mods? | **Yes**, LuaSocket (`socket` global). | Game's own `lua/vehicle/protocols.lua` calls `socket.udp()` in the vehicle VM; `lua/common/libs/luasocket` ships. |
| Where does the data live? | The **vehicle** Lua VM: `electrics.values` (nested tables allowed, e.g. `wheelThermals`). | `lua/vehicle/guistreams.lua` streams `electrics.values` to the UI. |
| Do we need a game-engine (GE) extension? | **No.** A file in `lua/vehicle/extensions/auto/` is loaded into every vehicle automatically. | `lua/vehicle/main.lua`: `extensions.loadModulesInDirectory("lua/vehicle/extensions/auto")`. |
| How to send only for the player's car? | `playerInfo.firstPlayerSeated` guard. | Same guard used by `protocols.lua`. |
| JSON encoding available in the vehicle VM? | `jsonEncode()` global. | `lua/common/utils.lua`. |
| Wall-clock rate limiting? | `HighPerfTimer()` global. | Used by `protocols.lua` so slow-motion does not change the rate. |
| Existing built-in telemetry? | OutGauge (fixed binary struct) and MotionSim, off by default, enabled in Options. | `lua/vehicle/protocols/outgauge.lua`. Our JSON mod is a superset: it sends *every* electrics channel. |
| Is there a mod-friendly place for settings? | Not needed yet; all config lives in the dashboard (per plan). | |

## Design consequence

Simpler than planned: **one Lua file, no GE side.** The mod is
`mod/lua/vehicle/extensions/auto/thrashdash_telemetry.lua`; it sends two JSON datagram types to `127.0.0.1:4568`:

- `catalog` every 2 s: `{v, type, vehicle, channels:{name: lua_type}}`
- `frame` at 30 Hz: `{v, type, n, vehicle, electrics:{...}}`

Known channel names seen across the game's code include: `rpm`, `wheelspeed`, `airspeed`, `gear`, `gearIndex`,
`throttle`, `brake`, `clutch`, `fuel`, `fuelVolume`, `fuelCapacity`, `watertemp`, `oiltemp`, `turboBoost`,
`engineLoad`, `parkingbrake`, `signal_L`, `signal_R`, `hazard`, `highbeam`, `lights`, `abs`/`absActive`,
`esc`/`escActive`, `tcs`/`tcsActive`, `checkengine`, `lowfuel`, `oil`, `steering_input`, `wheelThermals`.
The real list comes from step 3 below.

## Verified in-game (2026-11-08, 0.40.5, Linux native)

- The mod loads, sends, and the listener receives live data. Capture: `phase0-capture.json` (car `/vehicles/etkc/`).
- 128 top-level channels, 139 once flattened (`wheelThermals.<FL|FR|RL|RR>.<brakeCoreTemperature|brakeSurfaceTemperature|brakeThermalEfficiency>`).
- Value types: numbers (ints and floats), booleans (`checkengine`, `lowfuel`, `ignition`, `running`, `escActive`...) and strings (`gear` = `"N"`, `gearboxMode` = `"realistic"`).
- Units observed: `watertemp`/`oiltemp` in deg C, `fuel` 0..1, `fuelVolume`/`fuelCapacity` litres, `wheelspeed` m/s (needs conversion to km/h or mph on the display side), `maxrpm` 5000.
- Roughly a third of the channels are noise for a dashboard (door/hood/trunk couplers, sunvisors, `*_filament`, `dualAxis*`, `nop`), so the channel picker needs search and grouping.
- The channel set differs per car (this car has no `turboBoost`), so every widget must cope with a missing channel.
- `gear` is a string on this car (`"N"`, presumably `"D"`, `"R"`, `"M1"`...), `gearIndex` is numeric: the gear widget must accept both.

## Second vehicle (`/vehicles/midtruck/`, 144 flattened channels)

Compared against the ETK list above (the ETK capture file itself was overwritten by a re-run; the key list was compared from the earlier output):

- About 110 channels are common to both cars (rpm, wheelspeed, throttle, brake, clutch, fuel, temps, gear, lights, signals, parking brake, ABS, wheelThermals...). These are the core dashboard set.
- Truck-only (~34): drivetrain and diff state (`axle_*`, `propshaft`, `mode4WD`, `rangebox_state`, `*_lock`, `transfercase_lock`, `exhaustFlow`, `gear_M`, `hPatternAxis*`), plus extra `*_filament` and door couplers.
- ETK-only (~29): `esc`, `escActive`, `tcs`, `tcsActive`, `hasESC`, `hasTCS`, `isTCBrakeActive`, `isYCBrakeActive`, `turboBoostMax`, `drl`, `twoStep`, `lockupClutchRatio`.
- **`gear` changes type between cars**: string `"N"` on the automatic ETK, number `1` on the manual truck. `gearIndex` is numeric on both. Confirms the gear widget must accept both and that widgets must tolerate missing channels.
- `gearboxMode` was `realistic` on one car and `arcade` on the other, so it is a driver setting, not a car property.
- The log shows every vehicle loads the mod (`telemetry ready for ...`) but only the seated one sends.

## Resolved

- The game follows a **symlinked** mod folder (log: `mountEntry -- /mods/unpacked/thrashdash/`). Copying is not required.
- Frame-rate effect: not reported as a problem; will keep watching.
5. Tier B data (per-wheel tyre temperature/pressure, damage) is **not** in `electrics`; it needs extra vehicle-side code, so it is deferred to phase 3 as planned.
6. Tier C (lap timing) has no source in the vehicle VM; deferred as planned.

## How to run the spike

Install the mod. Either symlink (try first):

    ln -s <repo>/mod ~/.local/share/BeamNG/BeamNG.drive/current/mods/unpacked/thrashdash

or copy (use this if the game ignores the symlink; re-copy after each change):

    cp -r <repo>/mod ~/.local/share/BeamNG/BeamNG.drive/current/mods/unpacked/thrashdash

Then:

1. In a terminal start the listener (stays running):

       python3 <repo>/tools/spike_listen.py --dump <repo>/docs/phase0-capture.json

2. Start BeamNG, load any map, spawn a car and sit in it. The listener should print the channel catalog, then a live line with rpm, speed, gear, fuel and water temperature.
3. Try a second, different car (ideally a truck and an EV). The catalog should differ.
4. If nothing arrives, look in `~/.local/share/BeamNG/BeamNG.drive/current/beamng.log` for lines containing `thrashdash`:
   - `telemetry ready for ...` means it loaded.
   - `could not create UDP socket` or `send failed` gives the reason.
   - No `thrashdash` line at all means the game did not load the file (the symlink/copy step).

To test the listener without the game: run `python3 tools/spike_fake_sender.py` in another terminal.
