# -*- mode: python ; coding: utf-8 -*-

import os

# 获取项目根目录
project_root = os.path.dirname(os.path.abspath(SPEC))
build_metadata = os.path.join(project_root, 'src', 'build-info.json')
if not os.path.isfile(build_metadata):
    raise FileNotFoundError('Run generate_version.ps1 before building the executable.')

a = Analysis(
    ['src/app.py'],
    pathex=[os.path.join(project_root, 'src')],
    binaries=[],
    datas=[
        # Keep build identity inside the executable as well as beside it in the ZIP.
        (build_metadata, '.'),
        (os.path.join(project_root, 'src', 'resources', 'install_update.ps1'), 'resources'),
        # 添加翻译文件
        (os.path.join(project_root, 'src', 'locales', 'zh.yml'), 'locales'),
        (os.path.join(project_root, 'src', 'locales', 'en.yml'), 'locales'),
        (os.path.join(project_root, 'src', 'locales', 'ja.yml'), 'locales'),
        # 添加图标文件
        (os.path.join(project_root, 'docs', 'images', 'fish-cake.ico'), 'docs/images'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='DG-LAB-VRCOSC',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(project_root, 'docs', 'images', 'fish-cake.ico'),
)
