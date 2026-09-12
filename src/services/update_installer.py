"""Download and stage an official package before the running app exits."""
import asyncio
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
import zipfile

import aiohttp

PACKAGE_FILES = {'DG-LAB-VRCOSC.exe', 'build-info.json', 'build-info.txt'}
MAX_PACKAGE_SIZE = 512 * 1024 * 1024
MAX_EXTRACTED_SIZE = 1024 * 1024 * 1024


def _remove_workdir(work):
    work = Path(work).resolve()
    if work.parent != Path(tempfile.gettempdir()).resolve() or not work.name.startswith('dglab-update-'):
        raise ValueError('Refusing to remove an unexpected update directory')
    shutil.rmtree(work, ignore_errors=True)


def discard_update(stage):
    stage = Path(stage).resolve()
    if stage.name != 'stage':
        raise ValueError('Unexpected update staging directory')
    _remove_workdir(stage.parent)


def read_build_info():
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    try:
        data = json.loads((base / 'build-info.json').read_text(encoding='utf-8-sig'))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def validate_download_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname != 'github.com'
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not parsed.path.startswith('/ccvrc/DG-LAB-VRCOSC/releases/download/')):
        raise ValueError('Update packages must come from this project’s official GitHub Releases')
    return url


def unpack_update(archive, stage):
    """Validate the entire ZIP, then extract only application-owned files."""
    stage = Path(stage)
    stage.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        if sum(item.file_size for item in members) > MAX_EXTRACTED_SIZE:
            raise ValueError('Update package is too large')
        seen = set()
        for item in members:
            path = PurePosixPath(item.filename.replace('\\', '/'))
            if (path.is_absolute() or '..' in path.parts or ':' in item.filename
                    or stat.S_ISLNK(item.external_attr >> 16)):
                raise ValueError('Update package contains an unsafe path')
            if item.filename in seen:
                raise ValueError('Update package contains duplicate entries')
            seen.add(item.filename)
        if 'DG-LAB-VRCOSC.exe' not in seen:
            raise ValueError('Update package does not contain DG-LAB-VRCOSC.exe at its root')
        bad = package.testzip()
        if bad:
            raise ValueError(f'Update package is corrupt: {bad}')
        # Never extract bundled settings or arbitrary files over user data.
        for name in PACKAGE_FILES & seen:
            item = package.getinfo(name)
            if item.is_dir():
                raise ValueError(f'Expected an update file: {name}')
            with package.open(item) as source, (stage / name).open('wb') as target:
                shutil.copyfileobj(source, target)
        with (stage / 'DG-LAB-VRCOSC.exe').open('rb') as executable:
            if executable.read(2) != b'MZ':
                raise ValueError('Update executable is not a Windows executable')
    return stage


async def prepare_update(asset, progress=None):
    url = validate_download_url(asset['browser_download_url'])
    work = Path(tempfile.mkdtemp(prefix='dglab-update-'))
    archive = work / 'package.zip'
    expected_size = asset.get('size')
    if not isinstance(expected_size, int) or isinstance(expected_size, bool) or not 0 < expected_size <= MAX_PACKAGE_SIZE:
        _remove_workdir(work)
        raise ValueError('Update package has an invalid size')
    try:
        timeout = aiohttp.ClientTimeout(total=600, connect=15, sock_read=30)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            async with session.get(url, headers={'User-Agent': 'DG-LAB-VRCOSC-Updater'}) as response:
                response.raise_for_status()
                final_host = response.url.host
                if final_host not in {'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}:
                    raise ValueError('GitHub redirected the package to an unexpected host')
                digest = hashlib.sha256()
                received = 0
                with archive.open('wb') as target:
                    async for chunk in response.content.iter_chunked(256 * 1024):
                        received += len(chunk)
                        if received > expected_size:
                            raise ValueError('Downloaded package exceeds its advertised size')
                        target.write(chunk)
                        digest.update(chunk)
                        if progress:
                            progress(int(received * 100 / expected_size))
                if received != expected_size:
                    raise ValueError('Update download is incomplete')
                expected_digest = asset.get('digest')
                if expected_digest and expected_digest != 'sha256:' + digest.hexdigest():
                    raise ValueError('Update package checksum does not match GitHub')
        stage = work / 'stage'
        # Wait for validation to finish even on cancellation before removing its files.
        worker = asyncio.create_task(asyncio.to_thread(unpack_update, archive, stage))
        cancelled = False
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                cancelled = True
        worker.result()
        if cancelled:
            raise asyncio.CancelledError
        return stage
    except BaseException:
        _remove_workdir(work)
        raise


def launch_installer(stage):
    if not getattr(sys, 'frozen', False):
        raise RuntimeError('Automatic installation is only available in the packaged application')
    stage = Path(stage).resolve(strict=True)
    if not (stage / 'DG-LAB-VRCOSC.exe').is_file():
        raise ValueError('The update has not been staged')
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    # This copy survives PyInstaller deleting its extraction directory on exit.
    script = stage.parent / 'install_update.ps1'
    shutil.copyfile(base / 'resources' / 'install_update.ps1', script)
    command = [
        'powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', str(script), '-StagePath', str(stage),
        '-InstallPath', str(Path(sys.executable).resolve().parent),
        '-ParentProcessId', str(os.getpid()),
    ]
    return subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW)
