# -*- mode: python ; coding: utf-8 -*-

import os

block_cipher = None

# 获取项目根目录
project_root = os.path.dirname(os.path.abspath(SPEC))

a = Analysis(
    ['src/app.py'],
    pathex=[os.path.join(project_root, 'src')],
    binaries=[],
    datas=[
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
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
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
