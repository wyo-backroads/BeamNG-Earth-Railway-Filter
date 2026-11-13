# Menu-runner validation — 0.3.1

This version replaces the separate launcher, the blocked Lua startup attempt and
the manual PowerShell installer with Earth's existing companion Add-ons runner.

The integration test uses a disposable copy of the real signed dev.410 EXE and
calls its actual `Addons.install_companion` and `Addons.run_op(..., 'start')` code.
No replacement launcher or invented process-running Lua API is used.

Version 0.3.1 fixes the signed-file alignment check that rejected dev.410. That
file has two zero padding bytes between its Python archive and certificate table.
The patcher now accepts exactly the zero padding required for eight-byte
alignment, as specified in Microsoft's
[PE certificate-table format](https://learn.microsoft.com/en-us/windows/win32/debug/pe-format#the-attribute-certificate-table-image-only).
Nonzero padding and unexpected offsets remain rejected.

Checks include:

- Installing the single release file using the native companion service.
- Starting it via the native Start action with the real packaged Python.
- Returning from setup before the normal app closes, so the app's parent-process
  watcher cannot terminate the deferred replacement helper.
- Keeping the running server executable untouched until it closes.
- Applying the patch after a normal app shutdown.
- Reading the native Options values on subsequent starts, without starting the
  setup companion again.
- Main-process and spawned-worker classification with the toggle on and off.
- Distinct mesh and plan cache keys for those settings.
- Returning to stock behavior when the companion file is removed.
- Restoring the original signed EXE through the menu action with an exact
  original SHA-256 checksum match.
- Source backup, idempotent setup, update protection and migration of an owned
  patch using the verified original backup rather than patching patched code.

The user's actual server executable was not changed by the automated tests.
After installing and starting the local add-on through the normal menu, the user
confirmed that version 0.3.1 worked on November 13, 2026.
