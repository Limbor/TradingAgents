# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for the TradingAgents desktop backend sidecar.
#
# Build (from repo root, inside the project venv):
#     pyinstaller packaging/tradingagents-backend.spec --noconfirm
#
# Produces ``dist/tradingagents-backend`` (a single self-contained binary).
# scripts/build_desktop.sh renames it with the Rust target triple and drops
# it into ``src-tauri/binaries/`` where Tauri picks it up as a sidecar.
#
# The tradingagents package discovers skills dynamically (importlib), and the
# data/LLM stack (akshare, pandas, langchain, langgraph, uvicorn, ...) pulls
# in submodules and data files that static analysis misses — hence the broad
# collect_submodules / collect_all below.

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

packaging_dir = Path(SPECPATH)
project_root = packaging_dir.parent

hiddenimports = []
datas = []
binaries = []

# The application package: skills are auto-discovered at runtime, so every
# submodule must be bundled even if not statically imported.
hiddenimports += collect_submodules("tradingagents")
datas += collect_data_files("tradingagents.skills", includes=["*/SKILL.md"])

# uvicorn's protocol/loop implementations are imported by name at runtime.
hiddenimports += collect_submodules("uvicorn")

# Packages that ship data files and/or lazily import submodules. collect_all
# returns (datas, binaries, hiddenimports) for each.
for pkg in (
    "akshare",
    "langchain",
    "langchain_core",
    "langchain_community",
    "langgraph",
    "tiktoken",
    "tiktoken_ext",
):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hidden
    except Exception:
        # Optional providers may not be installed in every environment; skip
        # them rather than fail the whole build.
        pass


block_cipher = None

a = Analysis(
    [str(packaging_dir / "backend_entry.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim heavy dev-only / plotting deps that the API server never needs.
        "matplotlib",
        "notebook",
        "IPython",
        "pytest",
    ],
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
    a.datas,
    [],
    name="tradingagents-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
