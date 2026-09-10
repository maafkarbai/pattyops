# Kitchen computer: graphical setup and daily use

The Windows desktop app is the kitchen entry point. Staff do not need a terminal,
Python, Bun, Docker, or a database administration tool when using the packaged installer.

## Portable app: copy and open

Copy the entire built `dist/PattyOps Kitchen` folder, including `_internal`, to a
permanent folder on the Windows kitchen computer. Double-click
`PattyOps Kitchen.exe`. Python and developer tools are not needed. Keep the
executable inside its folder. Use Setup as described below. Windows can create
a desktop shortcut to the executable.

## Installer option

Use this route when your maintainer has built `PattyOps-Kitchen-Setup.exe`.

1. The deployment owner supplies `PattyOps-Kitchen-Setup.exe`, the trusted trained
   model, and this kitchen's cloud address, ID, and connection key.
2. Double-click the installer and follow its wizard. It creates desktop and Start
   menu shortcuts and opens PattyOps Kitchen.
3. Open **Setup**. Browse to the supplied model and select camera `0` (try `1` for
   a second camera). Keep the default saved-records location unless migrating an
   existing database. For video testing, browse to a video instead.
4. Enter the HTTPS cloud address, kitchen ID, and connection key, then click
   **Save setup**. Leave all cloud fields empty for a local-only installation.
5. Confirm the cloud connection message, click **Start kitchen**, and check the
   live preview. Review a few events in **Saved records** and **Cloud event logs**.

Keep the model in a permanent local folder; setup saves its location. Models and
credentials are supplied separately and are never embedded in the public installer.
The cloud API and PostgreSQL must already be provisioned by the deployment owner.
Update the cloud API to this release for older-page browsing and event filters.
This Windows installer installs the kitchen application, not the cloud infrastructure.

## During a shift

- Open **PattyOps Kitchen** from the desktop and click **Start kitchen**.
- Keep the app open. **Stop kitchen** finishes and saves the current session.
- **Saved records** lets you browse cooking events, sessions, and patties without
  modifying them. Use Refresh, Older, Newer, and event filters. Double-click a row
  for its full values. Export this page saves up to 100 rows as CSV.
- **Cloud event logs** shows this kitchen's uploaded cooking events, including
  older history. It requires connectivity and valid cloud credentials.
- **Support logs** shows local diagnostic output. It does not expose remote
  Docker, PostgreSQL, or cloud host system logs; those remain with the deployment
  owner's hosting tools.
- Network outages do not stop local detection. Uploads retry while the app is open.
  They also run when tracking is stopped, so leave the app open to finish syncing.
- After a computer restart, reopen the app and click Start kitchen. This release
  does not start unattended at boot.

## Storage and updates

Settings, logs, and the default database are under `%LOCALAPPDATA%\PattyOps` for
that Windows user. Use the same Windows account for every shift. The connection
key is stored in the user's settings file and should only be shared with support
through your approved secure channel. Record views and exports contain no key.

Reinstalling/updating the app leaves this data intact. Back up the database when
tracking and synchronization are stopped. Do not run the old inference scripts or
another installation against the same database at the same time. To review an
existing deployment, choose its existing database file in Setup; the app does not
silently move or merge previous records.

## Build computer only

The supplied `PattyOps-icon.svg` is the source for the app's branding. The desktop
build exports `assets/pattyops.png` for the header and `assets/pattyops.ico` for
Windows and the installer. These assets are bundled with the app; kitchen staff
do not need the original Downloads folder. `build_brand_assets.py` regenerates
them without changing the original vector artwork.

Maintainers prepare `.venv` with `bun run setup`, install [Inno Setup 6](https://jrsoftware.org/isdl.php), then run
`bun run desktop:build`. The output is `dist/PattyOps-Kitchen-Setup.exe`.
PyInstaller bundles Python, Tk, and inference dependencies; Inno Setup provides
installation and shortcuts. The build computer must be Windows with the same
architecture as the kitchen computer. This is a large inference application;
allow sufficient disk space for PyTorch and the model.

To build only the portable folder, use `bun run desktop:build -- -Portable`.
This route does not need Inno Setup.

For development in an already prepared checkout, double-click `Open PattyOps.vbs`
to launch without a console. This development shortcut requires the checkout's
`.venv`; distribute the installer to kitchen staff.

Before distribution, test the built installer on a clean kitchen-like Windows
computer without developer tools: installation, preview with the actual model and
USB camera, Stop/restart, reboot, record browsing, offline recording and recovery,
cloud credentials, and upgrade preserving data. Code tests do not establish model
accuracy or clean-machine packaging compatibility. Sign the release installer with
your organization's signing certificate when distributing it.
