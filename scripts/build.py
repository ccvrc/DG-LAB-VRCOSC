"""Build using Python/Windows DLLs rather than unrelated developer-tool DLLs."""
import os
from pathlib import Path
import sys


def main():
    project_root = Path(__file__).resolve().parents[1]
    os.chdir(project_root)
    if sys.platform == 'win32':
        # Qt uses the Windows ICU library. A third-party ICU/OpenSSL directory
        # on PATH can otherwise be collected and break the frozen application.
        windows = Path(os.environ['SystemRoot'])
        os.environ['PATH'] = os.pathsep.join([
            str(Path(sys.executable).parent), sys.base_prefix,
            str(windows / 'System32'), str(windows),
        ])
    from PyInstaller.__main__ import run
    run(['--noconfirm', 'DG-LAB-VRCOSC.spec', *sys.argv[1:]])


if __name__ == '__main__':
    main()
