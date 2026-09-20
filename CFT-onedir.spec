# -*- mode: python ; coding: utf-8 -*-
# 폴더형(onedir) 빌드.
#
# 왜 onefile이 아니라 이것인가: onefile은 실행할 때마다 42MB를 임시 폴더에
# 통째로 풀고 끝나면 지운다. 그래서 몇 번을 실행해도 창이 뜨기까지 매번
# 1.6초가 걸린다(실측). onedir은 이미 풀려 있어 두 번째 실행부터 0.8초다.
# 대신 파일이 183개로 흩어지므로, 사용자에게는 설치 프로그램(installer/)으로
# 묶어서 준다 - Program Files에 풀고 바로가기만 보이게 하는 방식이다.


a = Analysis(
    ['app/main.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Cloudflare Tunnel GUI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/cloudflare_logo.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='CloudflareTunnelGUI',
)
