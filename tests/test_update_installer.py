import asyncio
import hashlib
import io
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
import zipfile

from services import update_installer as installer


def package_bytes(entries=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as package:
        for name, data in (entries or {'DG-LAB-VRCOSC.exe': b'MZ-test', 'build-info.json': b'{}'}).items():
            package.writestr(name, data)
    return buffer.getvalue()


class PackageTests(unittest.TestCase):
    def test_extracts_only_application_files_and_leaves_config_out(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'update.zip'
            archive.write_bytes(package_bytes({'DG-LAB-VRCOSC.exe': b'MZ-new', 'settings.yml': b'bad: config'}))
            stage = installer.unpack_update(archive, root / 'stage')
            self.assertEqual((stage / 'DG-LAB-VRCOSC.exe').read_bytes(), b'MZ-new')
            self.assertFalse((stage / 'settings.yml').exists())

    def test_rejects_traversal_symlinks_and_non_executable_packages(self):
        bad_entries = [
            {'../evil': b'data', 'DG-LAB-VRCOSC.exe': b'MZ'},
            {'C:/evil': b'data', 'DG-LAB-VRCOSC.exe': b'MZ'},
            {'/evil': b'data', 'DG-LAB-VRCOSC.exe': b'MZ'},
            {'DG-LAB-VRCOSC.exe': b'error page'},
            {'source.py': b'print(1)'},
        ]
        link = zipfile.ZipInfo('link')
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        bad_entries.append({link: b'target', 'DG-LAB-VRCOSC.exe': b'MZ'})
        for entries in bad_entries:
            with self.subTest(entries=entries), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = root / 'update.zip'
                archive.write_bytes(package_bytes(entries))
                with self.assertRaises(ValueError):
                    installer.unpack_update(archive, root / 'stage')

    def test_cleanup_refuses_non_updater_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                installer.discard_update(root / 'stage')
            self.assertTrue(root.is_dir())

    def test_installer_script_survives_frozen_temp_cleanup_and_quotes_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frozen = root / 'frozen'
            (frozen / 'resources').mkdir(parents=True)
            (frozen / 'resources' / 'install_update.ps1').write_text('param()', encoding='utf-8')
            stage = root / "update's path" / 'stage'
            stage.mkdir(parents=True)
            (stage / 'DG-LAB-VRCOSC.exe').write_bytes(b'MZ-new')
            with patch.object(sys, 'frozen', True, create=True), patch.object(sys, '_MEIPASS', str(frozen), create=True), \
                 patch.object(sys, 'executable', str(root / "installed app's" / 'DG-LAB-VRCOSC.exe')), \
                 patch.object(installer.subprocess, 'Popen') as launch:
                installer.launch_installer(stage)
            args = launch.call_args.args[0]
            self.assertIn('-File', args)
            self.assertIn(str(stage), args)
            self.assertEqual(launch.call_args.kwargs['creationflags'], subprocess.CREATE_NO_WINDOW)
            copied = Path(args[args.index('-File') + 1])
            self.assertTrue(copied.is_file())
            self.assertNotIn(frozen, copied.parents)


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    def session(self, body):
        response = MagicMock()
        response.url.host = 'release-assets.githubusercontent.com'

        async def chunks(_):
            yield body

        response.content.iter_chunked = chunks
        session = MagicMock()
        session.get.return_value.__aenter__.return_value = response
        factory = MagicMock()
        factory.return_value.__aenter__.return_value = session
        return factory

    async def test_download_checks_digest_and_size_before_staging(self):
        body = package_bytes()
        asset = {'browser_download_url': 'https://github.com/ccvrc/DG-LAB-VRCOSC/releases/download/v0.4.9/DG-LAB-VRCOSC.zip',
                 'size': len(body), 'digest': 'sha256:' + hashlib.sha256(body).hexdigest()}
        progress = []
        with patch.object(installer.aiohttp, 'ClientSession', self.session(body)):
            stage = await installer.prepare_update(asset, progress.append)
        self.assertTrue((stage / 'DG-LAB-VRCOSC.exe').is_file())
        self.assertEqual(progress[-1], 100)
        installer.discard_update(stage)
        for change in ({'size': len(body) + 1}, {'digest': 'sha256:' + '0' * 64}):
            with self.subTest(change=change), patch.object(installer.aiohttp, 'ClientSession', self.session(body)):
                with self.assertRaises(ValueError):
                    await installer.prepare_update({**asset, **change})

    async def test_repeated_cancel_waits_for_unpack_thread_before_cleanup(self):
        body = package_bytes()
        entered, finish = threading.Event(), threading.Event()
        stages = []

        def unpack(archive, stage):
            stages.append(stage)
            entered.set()
            finish.wait(5)
            stage.mkdir()
            (stage / 'DG-LAB-VRCOSC.exe').write_bytes(b'MZ')

        asset = {'browser_download_url': 'https://github.com/ccvrc/DG-LAB-VRCOSC/releases/download/v0.4.9/DG-LAB-VRCOSC.zip', 'size': len(body)}
        with patch.object(installer.aiohttp, 'ClientSession', self.session(body)), patch.object(installer, 'unpack_update', unpack):
            task = asyncio.create_task(installer.prepare_update(asset))
            while not entered.is_set():
                await asyncio.sleep(0.01)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            self.assertTrue(stages[0].parent.is_dir())
            finish.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(stages[0].parent.exists())


@unittest.skipUnless(sys.platform == 'win32', 'Windows installer integration')
class PowerShellInstallerTests(unittest.TestCase):
    script = Path(__file__).resolve().parents[1] / 'src' / 'resources' / 'install_update.ps1'

    def run_installer(self, stage, target):
        return subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                               '-File', str(self.script), '-StagePath', str(stage), '-InstallPath', str(target), '-NoRestart'],
                              capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)

    def test_replace_preserves_settings_and_cleans_private_workdir(self):
        with tempfile.TemporaryDirectory(prefix='dglab-update-') as directory, tempfile.TemporaryDirectory() as destination:
            stage, target = Path(directory) / 'stage', Path(destination) / "app's folder"
            stage.mkdir()
            target.mkdir()
            (stage / 'DG-LAB-VRCOSC.exe').write_bytes(b'MZ-new')
            (target / 'DG-LAB-VRCOSC.exe').write_bytes(b'MZ-old')
            (target / 'settings.yml').write_text('keep: me')
            result = self.run_installer(stage, target)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((target / 'DG-LAB-VRCOSC.exe').read_bytes(), b'MZ-new')
            self.assertEqual((target / 'settings.yml').read_text(), 'keep: me')
            self.assertFalse(Path(directory).exists())

    def test_mid_install_failure_restores_previous_executable(self):
        with tempfile.TemporaryDirectory(prefix='dglab-update-') as directory, tempfile.TemporaryDirectory() as destination:
            stage, target = Path(directory) / 'stage', Path(destination)
            (stage / 'backup').mkdir(parents=True)
            (stage / 'DG-LAB-VRCOSC.exe').write_bytes(b'MZ-new')
            (stage / 'build-info.json').write_text('new')
            (target / 'DG-LAB-VRCOSC.exe').write_bytes(b'MZ-old')
            (target / 'build-info.json').write_text('old')
            (stage / 'backup' / 'build-info.json').write_text('force a collision')
            result = self.run_installer(stage, target)
            self.assertEqual(result.returncode, 1)
            self.assertEqual((target / 'DG-LAB-VRCOSC.exe').read_bytes(), b'MZ-old')
            self.assertEqual((target / 'build-info.json').read_text(), 'old')
            self.assertTrue((stage / 'update-error.txt').exists())
