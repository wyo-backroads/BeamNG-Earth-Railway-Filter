# BeamNG.Earth Railway Filter

Hide abandoned railway tracks in BeamNG.Earth with a simple menu toggle. Active
and disused railways stay visible, and you keep using the normal Earth launcher.

## Install

1. Start BeamNG.Earth normally and open **Earth settings > Add-ons > Add from GitHub**.
2. Paste **`wyo-backroads/BeamNG-Earth-Railway-Filter`** and click **Install**.
3. Click **Start** once to set up the filter.
4. When prompted, close Earth completely, wait a few seconds, then launch it normally.

No coding, Python installation, scripts to run yourself, or extra launcher required.

## Toggle

Open the add-on's **Options**, change **Hide abandoned railways**, and save.
Restart Earth and reload the level to apply the change. You only need **Start**
for initial setup; "not running" afterward is normal.

## Restore

Choose **Options > Server patch > Restore original server**, save, and click
**Start**. Restart Earth when prompted, then remove the add-on if desired.

**Windows only. Tested with BeamNG.Earth 1.9.1-beta.dev.410.** Setup patches your
local server and keeps an exact original-file backup. A patched EXE no longer
retains its publisher's signature; restoring the backup restores the signed original.

Independent community add-on. See [validation](VALIDATION.md) for test details.

For contributors: run `python build_release.py` to build the release file.
