"""A local package must find its installed Python command when Finder starts it."""
from pathlib import Path
import plistlib
import runpy
import shutil
import sys

import pytest


@pytest.mark.skipif(sys.platform!='darwin',reason='macOS package metadata')
def test_local_app_keeps_python_command_path(tmp_path,monkeypatch):
    project=tmp_path/'local project';desktop=project/'collector-desktop';desktop.mkdir(parents=True)
    script=desktop/'package_macos.py'
    shutil.copyfile(Path(__file__).resolve().parents[1]/'collector-desktop/package_macos.py',script)
    binary=desktop/'src-tauri/target/debug/mycelos-collector-desktop'
    binary.parent.mkdir(parents=True);binary.write_bytes(b'native-test-binary');binary.chmod(0o755)
    command=project/'.venv/bin/mycelos-collector'
    command.parent.mkdir(parents=True);command.write_text('#!/bin/sh\nexit 0\n');command.chmod(0o755)
    monkeypatch.delenv('MYCELOS_COLLECTOR_EXECUTABLE',raising=False)
    monkeypatch.setattr(sys,'argv',[str(script),'--profile','debug'])
    runpy.run_path(str(script),run_name='__main__')
    app=desktop/'dist/myCelos Sammler.app/Contents'
    with (app/'Info.plist').open('rb') as stream:info=plistlib.load(stream)
    assert info['LSEnvironment']['MYCELOS_COLLECTOR_EXECUTABLE']==str(command)
    assert (app/'MacOS/mycelos-collector-desktop').read_bytes()==binary.read_bytes()
