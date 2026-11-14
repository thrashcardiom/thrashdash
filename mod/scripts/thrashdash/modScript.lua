-- Run by the game's mod manager when the mod is mounted (core/modmanager.lua finds every /scripts/**/modScript.lua).
-- 'manual' keeps the extension loaded across level changes, and the mod manager loads every 'manual' extension right
-- after the modScripts (the same way BeamNG.Earth loads its own).
--
-- The trip log (lua/ge/extensions/thrashdash/trips.lua): reads the player's position and the world from BeamNG.Earth's
-- modding API and sends it to ThrashDash. It does nothing when BeamNG.Earth is not installed or its level is not loaded.
setExtensionUnloadMode('thrashdash_trips', 'manual')
