# Build on Windows using the project's isolated Python environment.
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

a = Analysis(
    ['kitchen_app.py'],
    datas=collect_data_files('ultralytics') + [('assets/pattyops.png', 'assets'), ('assets/pattyops.ico', 'assets')],
    hiddenimports=collect_submodules('ultralytics'),
    excludes=['pytest', 'IPython', 'tensorboard'],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='PattyOps Kitchen',
          console=False, debug=False, strip=False, upx=False, icon='assets/pattyops.ico')
coll = COLLECT(exe, a.binaries, a.datas, name='PattyOps Kitchen', strip=False, upx=False)
