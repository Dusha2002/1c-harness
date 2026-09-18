"""Run after installing .[windows] and pyinstaller, on the target platform."""
import platform
import shutil
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
if platform.system() != 'Windows':
    raise SystemExit('The installer currently targets Windows x64 only')
subprocess.run([
    'pyinstaller', '--noconfirm', '--clean', '--onefile', '--name', 'onec-harness-bridge',
    '--collect-all', 'onec_harness', '--hidden-import', 'win32com.client',
    '--hidden-import', 'pythoncom', '--hidden-import', 'pywintypes',
    str(root / 'scripts' / 'bridge_entry.py'),
], cwd=root, check=True)
target = root / 'desktop/src-tauri/binaries/onec-harness-bridge-x86_64-pc-windows-msvc.exe'
target.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(root / 'dist/onec-harness-bridge.exe', target)
