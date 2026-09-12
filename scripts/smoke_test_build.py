"""Start a packaged Windows application in isolation and verify GUI startup."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import psutil


STARTUP_MARKER = 'Application startup complete'
STARTUP_TIMEOUT = 30
STABLE_SECONDS = 1
TEMP_PREFIX = 'dglab-build-smoke-'


def _remember_tree(process, tracked):
    """Keep Process objects so psutil can detect PID reuse before termination."""
    tracked.add(process)
    try:
        tracked.update(process.children(recursive=True))
    except psutil.NoSuchProcess:
        pass


def _is_owned_executable(process, executable):
    try:
        return process.is_running() and Path(process.exe()).resolve() == executable
    except psutil.NoSuchProcess:
        return False


def _stop_process_tree(process, tracked, executable):
    _remember_tree(process, tracked)
    # Never terminate by image name or PID alone: every process must belong to
    # our recorded tree and still run the executable copied into our temp dir.
    matching = [item for item in tracked if _is_owned_executable(item, executable)]
    matching.sort(key=lambda item: item.pid == process.pid)
    for item in matching:
        try:
            if _is_owned_executable(item, executable):
                item.terminate()
        except psutil.NoSuchProcess:
            pass
    _, remaining = psutil.wait_procs(matching, timeout=3)
    for item in remaining:
        try:
            if _is_owned_executable(item, executable):
                item.kill()
        except psutil.NoSuchProcess:
            pass
    _, remaining = psutil.wait_procs(remaining, timeout=3)
    if any(_is_owned_executable(item, executable) for item in remaining):
        raise RuntimeError('A smoke-test process did not exit after termination')
    process.wait(timeout=3)


def _read_output(work):
    files = [work / 'process-output.log', *sorted((work / 'logs').glob('*.log'))]
    return '\n'.join(path.read_text(encoding='utf-8', errors='replace')
                     for path in files if path.is_file())


def _check_private_directory(work, temp_root):
    if work.resolve() != work or work.parent != temp_root or not work.name.startswith(TEMP_PREFIX):
        raise RuntimeError(f'Refusing to clean an unexpected smoke-test directory: {work}')


def smoke_test(executable):
    if sys.platform != 'win32':
        raise RuntimeError('Packaged application smoke tests require Windows')
    source = Path(executable).resolve(strict=True)
    if not source.is_file() or source.suffix.lower() != '.exe':
        raise ValueError('Provide the path to a built Windows executable')

    temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
    temporary = tempfile.TemporaryDirectory(prefix=TEMP_PREFIX, dir=temp_root)
    work = Path(temporary.name).resolve(strict=True)
    _check_private_directory(work, temp_root)
    process = None
    tracked = set()
    output = ''
    try:
        target = work / 'DG-LAB-VRCOSC.exe'
        shutil.copy2(source, target)
        (work / 'settings.yml').write_text('auto_update: false\n', encoding='utf-8')
        runtime = work / 'runtime'
        runtime.mkdir()
        environment = dict(os.environ, QT_QPA_PLATFORM='offscreen', TEMP=str(runtime), TMP=str(runtime))
        with (work / 'process-output.log').open('wb') as output_file:
            process = psutil.Popen(
                [str(target)], cwd=work, env=environment,
                stdin=subprocess.DEVNULL, stdout=output_file, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            tracked.add(process)
            deadline = time.monotonic() + STARTUP_TIMEOUT
            marker_seen_at = None
            try:
                while time.monotonic() < deadline:
                    _remember_tree(process, tracked)
                    output = _read_output(work)
                    if 'Traceback (most recent call last):' in output:
                        raise RuntimeError('The packaged application logged an exception during startup')
                    if process.poll() is not None:
                        raise RuntimeError(f'The packaged application exited during startup: {process.returncode}')
                    now = time.monotonic()
                    if STARTUP_MARKER in output:
                        if marker_seen_at is None:
                            marker_seen_at = now
                        elif now - marker_seen_at >= STABLE_SECONDS:
                            print('Packaged application smoke test passed: startup completed.')
                            return
                    time.sleep(0.1)
                raise RuntimeError(f'The startup marker was not followed by stable startup within {STARTUP_TIMEOUT} seconds')
            finally:
                _stop_process_tree(process, tracked, target.resolve())
    except Exception:
        if output:
            print('Smoke-test startup output:', file=sys.stderr)
            print('\n'.join(output.splitlines()[-40:]), file=sys.stderr)
        raise
    finally:
        # The only recursive cleanup is our checked, direct child of temp_root.
        _check_private_directory(work, temp_root)
        temporary.cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', help='Path to dist/DG-LAB-VRCOSC.exe')
    args = parser.parse_args()
    try:
        smoke_test(args.executable)
    except Exception as exc:
        print(f'Packaged application smoke test failed: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
