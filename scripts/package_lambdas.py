"""Build dist/{webhook,scheduler,exporter}.zip for Lambda, from any OS.

    python scripts/package_lambdas.py

Each zip holds the service's handler.py, the shared/ package, and its
requirements installed for Lambda's runtime (Linux x86_64, CPython 3.12).
Written with zipfile so entry names always use forward slashes; Windows
PowerShell 5.1's Compress-Archive can emit backslashes, which Lambda would
unpack as literal filenames like "shared\\db.py" and every import would fail.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ("webhook", "scheduler", "exporter")


def _add_tree(zf: zipfile.ZipFile, src: Path, prefix: str) -> None:
    for path in sorted(src.rglob("*")):
        if path.is_dir() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        zf.write(path, f"{prefix}{path.relative_to(src).as_posix()}")


def build(service: str, dist: Path) -> Path:
    svc_dir = ROOT / "services" / service
    with tempfile.TemporaryDirectory() as tmp:
        deps = Path(tmp)
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
             "-r", str(svc_dir / "requirements.txt"), "--target", str(deps),
             "--platform", "manylinux2014_x86_64", "--implementation", "cp",
             "--python-version", "3.12", "--only-binary=:all:"],
            check=True,
        )
        out = dist / f"{service}.zip"
        out.unlink(missing_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            _add_tree(zf, deps, "")
            _add_tree(zf, ROOT / "shared", "shared/")
            zf.write(svc_dir / "handler.py", "handler.py")
    return out


def main() -> int:
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    for service in SERVICES:
        out = build(service, dist)
        print(f"Built {out.relative_to(ROOT).as_posix()} ({out.stat().st_size / 1024:.0f} KiB)")
    build_dirs = [ROOT / "services" / s / "build" for s in SERVICES]
    for stale in build_dirs:
        shutil.rmtree(stale, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
