"""Create a local macOS app from the existing Cargo build."""
import argparse
import os
from pathlib import Path
import plistlib
import shutil
import sys

parser=argparse.ArgumentParser()
parser.add_argument('--profile',choices=('debug','release'),default='release')
parser.add_argument('--collector-executable',type=Path,help='Path to the installed Python collector command')
args=parser.parse_args()
if sys.platform!='darwin':
    raise SystemExit('This package step requires macOS')
root=Path(__file__).resolve().parent
explicit=args.collector_executable or os.environ.get('MYCELOS_COLLECTOR_EXECUTABLE')
candidates=[Path(explicit).expanduser()] if explicit else [
    root.parent/'.venv/bin/mycelos-collector',
    Path(sys.executable).parent/'mycelos-collector',
    Path(shutil.which('mycelos-collector') or '/nonexistent/mycelos-collector')]
collector=next((path.absolute() for path in candidates if path.is_file() and os.access(path,os.X_OK)),None)
if collector is None:
    raise SystemExit('Install mycelos first or provide --collector-executable')
binary=root/'src-tauri'/'target'/args.profile/'mycelos-collector-desktop'
if not binary.is_file():
    raise SystemExit('Build the requested Cargo profile first')
app=root/'dist'/'myCelos Sammler.app'
contents=app/'Contents'
(contents/'MacOS').mkdir(parents=True,exist_ok=True)
(contents/'Resources').mkdir(exist_ok=True)
shutil.copy2(binary,contents/'MacOS'/'mycelos-collector-desktop')
with (contents/'Info.plist').open('wb') as stream:
    plistlib.dump(dict(CFBundleName='myCelos Sammler',CFBundleDisplayName='myCelos Sammler',
        CFBundleExecutable='mycelos-collector-desktop',CFBundleIdentifier='com.mycelos.collector',
        CFBundlePackageType='APPL',CFBundleVersion='0.1.0',CFBundleShortVersionString='0.1.0',
        NSHighResolutionCapable=True,LSMinimumSystemVersion='11.0',
        LSEnvironment={'MYCELOS_COLLECTOR_EXECUTABLE':str(collector)}),stream)
print(app)
