"""Build the il2ks Windows installer: `uv run python packaging/windows/build.py` -> `dist/il2ks-setup-<version>.exe`.

What it does (doc 07 option B; the packaging decision is in packaging/windows/README.md):

1. downloads the pinned inputs from `pins.toml` and refuses any file whose SHA-256 differs (cached in the work folder);
2. builds the il2ks wheel and installs it, with every dependency at the exact version and hash in `uv.lock`, into a
   relocatable CPython 3.13 (python-build-standalone). The installer needs no internet at install time (NFR-OFF-1);
3. adds Caddy, the WinSW service wrapper (with its rendered XML) and the small command-line wrappers;
4. checks that the bundle starts (`il2ks --version`, imports of every native dependency);
5. compiles `il2ks.iss` with Inno Setup's ISCC.

Windows only (it runs the bundled python.exe and ISCC.exe). Needs `uv` on the PATH. Nothing here touches the machine
outside the work folder (default `build/windows`) and the output folder (default `dist`).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
import urllib.request
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
PINS_FILE = HERE / "pins.toml"
VERSION_FILE = REPO / "src" / "il2ks" / "__init__.py"
_VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)
_PIN_FIELDS = ("version", "url", "sha256")
_CHUNK = 1024 * 1024

# Folders and files of the python-build-standalone tree that il2ks never needs (saves about 40 MB unpacked).
PRUNE = (
    "tcl",
    "include",
    "libs",
    "Scripts",
    "Lib/test",
    "Lib/idlelib",
    "Lib/tkinter",
    "Lib/turtledemo",
    "Lib/ensurepip",
    "Lib/site-packages/pip",
    "DLLs/_tkinter.pyd",
    "DLLs/tcl86t.dll",
    "DLLs/tk86t.dll",
)
MAX_WORK_PATH = 120  # the longest file below stage/ is about 130 characters deep; Windows' limit is 260
SMOKE_IMPORTS = "import sqlite3, ssl, zoneinfo, django, granian, PIL, whitenoise, tzdata, il2ks"


class BuildError(RuntimeError):
    """Something the person building has to fix; the message says what."""


@dataclass(frozen=True, slots=True)
class Pin:
    """One downloaded input: where it comes from and the SHA-256 it must have."""

    name: str
    version: str
    url: str
    sha256: str

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[-1].replace("%2B", "+")


def parse_pins(text: str) -> dict[str, Pin]:
    """`pins.toml` -> {name: Pin}. Every table needs version, url and a 64-digit lowercase hex sha256."""
    raw = cast(dict[str, object], tomllib.loads(text))
    pins: dict[str, Pin] = {}
    for name, table in raw.items():
        if not isinstance(table, dict):
            raise BuildError(f"pins.toml: [{name}] must be a table")
        fields = cast(dict[str, object], table)
        values: dict[str, str] = {}
        for field in _PIN_FIELDS:
            value = fields.get(field)
            if not isinstance(value, str) or not value:
                raise BuildError(f"pins.toml: [{name}] needs {field} (a string)")
            values[field] = value
        if re.fullmatch(r"[0-9a-f]{64}", values["sha256"]) is None:
            raise BuildError(f"pins.toml: [{name}] sha256 must be 64 lowercase hex digits")
        if not values["url"].startswith("https://"):
            raise BuildError(f"pins.toml: [{name}] url must be https")
        pins[name] = Pin(name, values["version"], values["url"], values["sha256"])
    return pins


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(pin: Pin, downloads: Path) -> Path:
    """The pinned file in `downloads`, downloaded if it is not there yet. Raises BuildError on a hash mismatch."""
    downloads.mkdir(parents=True, exist_ok=True)
    target = downloads / pin.filename
    if target.is_file() and sha256_of(target) == pin.sha256:
        return target
    print(f"downloading {pin.name} {pin.version}: {pin.url}")
    partial = target.with_name(target.name + ".part")
    request = urllib.request.Request(pin.url, headers={"User-Agent": "il2ks-installer-build"})
    with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as out:
        shutil.copyfileobj(response, out)
    actual = sha256_of(partial)
    if actual != pin.sha256:
        partial.unlink(missing_ok=True)
        raise BuildError(
            f"{pin.name}: SHA-256 mismatch for {pin.url}\n  expected {pin.sha256}\n  got      {actual}\n"
            "The download was refused. If you meant to change the version, update pins.toml."
        )
    partial.replace(target)
    return target


def read_version(version_file: Path = VERSION_FILE) -> str:
    match = _VERSION_RE.search(version_file.read_text(encoding="utf-8"))
    if match is None:
        raise BuildError(f"no __version__ in {version_file}")
    return match.group(1)


def numeric_version(version: str) -> str:
    """`0.3.1rc2` -> `0.3.1.0`: the four-number form Windows file properties need."""
    numbers = [int(m) for m in re.findall(r"\d+", re.split(r"[A-Za-z+-]", version, maxsplit=1)[0])][:4]
    return ".".join(str(n) for n in [*numbers, 0, 0, 0, 0][:4])


def crlf(text: str) -> str:
    """Batch files want Windows line endings (LF only can break `goto` and labels)."""
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def run(
    cmd: Sequence[str | Path], *, cwd: Path | None = None, env: Mapping[str, str] | None = None, quiet: bool = False
) -> None:
    """Run a command, echoing it; `quiet` hides its stdout (uv export also prints the file it writes)."""
    print("+", " ".join(str(c) for c in cmd))
    try:
        subprocess.run(
            [str(c) for c in cmd],
            cwd=cwd,
            env=None if env is None else dict(env),
            stdout=subprocess.DEVNULL if quiet else None,
            check=True,
        )
    except FileNotFoundError as exc:
        raise BuildError(f"cannot run {cmd[0]}: {exc.filename} was not found") from exc
    except subprocess.CalledProcessError as exc:
        raise BuildError(f"command failed with exit code {exc.returncode}: {cmd[0]}") from exc


def extract_python(archive: Path, stage: Path) -> Path:
    """Unpack the python-build-standalone tarball (a single top folder `python/`) to `<stage>/python`."""
    target = stage / "python"
    with tarfile.open(archive) as tar:
        tar.extractall(stage, filter="data")
    if not (target / "python.exe").is_file():
        raise BuildError(f"{archive.name} did not unpack to python/python.exe")
    return target


def prune_python(python_dir: Path) -> None:
    for rel in PRUNE:
        path = python_dir / rel
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    for dist_info in (python_dir / "Lib" / "site-packages").glob("pip-*"):
        shutil.rmtree(dist_info, ignore_errors=True)


def install_app(uv: str, python_dir: Path, wheel: Path, requirements: Path) -> None:
    """Dependencies exactly as locked (hashes checked, binary wheels only), then il2ks itself without resolution."""
    python = python_dir / "python.exe"
    common = [uv, "pip", "install", "--python", python, "--compile-bytecode", "--no-cache"]
    run([*common, "--require-hashes", "--only-binary", ":all:", "-r", requirements])
    run([*common, "--no-deps", wheel])


def unpack_member(archive: Path, member: str, target: Path) -> None:
    with zipfile.ZipFile(archive) as zf, zf.open(member) as src, target.open("wb") as out:
        shutil.copyfileobj(src, out)


def build_stage(work: Path, pins: dict[str, Pin], downloads: Path, wheel: Path, requirements: Path, uv: str) -> Path:
    from winsw import render_service_xml

    stage = work / "stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    python_dir = extract_python(fetch(pins["python"], downloads), stage)
    prune_python(python_dir)
    install_app(uv, python_dir, wheel, requirements)
    shutil.rmtree(python_dir / "Scripts", ignore_errors=True)  # console scripts hold this build's python path
    # compile the standard library too: the program folder is read-only for ordinary users, and start-up is faster
    run([python_dir / "python.exe", "-m", "compileall", "-q", "-j", "0", python_dir / "Lib"])
    run([python_dir / "python.exe", "-c", SMOKE_IMPORTS])
    run([python_dir / "python.exe", "-m", "il2ks", "--version"])

    (stage / "bin").mkdir()
    caddy_zip = fetch(pins["caddy"], downloads)
    unpack_member(caddy_zip, "caddy.exe", stage / "bin" / "caddy.exe")
    (stage / "licenses").mkdir()
    unpack_member(caddy_zip, "LICENSE", stage / "licenses" / "Caddy-LICENSE.txt")

    (stage / "service").mkdir()
    shutil.copyfile(fetch(pins["winsw"], downloads), stage / "service" / "il2ks-service.exe")
    (stage / "service" / "il2ks-service.xml").write_text(render_service_xml(), encoding="utf-8")

    for name in ("il2ks.cmd", "il2ks-admin.cmd"):
        text = (HERE / "files" / name).read_text(encoding="utf-8")
        (stage / name).write_bytes(crlf(text).encode("utf-8"))
    for name in ("LICENSE", "NOTICE"):
        shutil.copyfile(REPO / name, stage / "licenses" / f"il2ks-{name}.txt")
    shutil.copyfile(python_dir / "LICENSE.txt", stage / "licenses" / "Python-LICENSE.txt")
    (stage / "BUILD-INFO.txt").write_text(build_info(pins), encoding="utf-8")
    return stage


def build_info(pins: Mapping[str, Pin]) -> str:
    lines = [f"il2ks {read_version()}", ""]
    lines += [
        f"{name}: {pin.version}  {pin.sha256}" for name, pin in pins.items() if name in {"python", "caddy", "winsw"}
    ]
    commit = os.environ.get("GITHUB_SHA")
    if commit:
        lines += ["", f"source commit: {commit}"]
    return "\r\n".join(lines) + "\r\n"


def ensure_iscc(work: Path, pins: dict[str, Pin], downloads: Path, explicit: Path | None) -> Path:
    """An Inno Setup compiler: the given one, or the pinned Inno Setup unpacked (not installed) with innounp."""
    if explicit is not None:
        if not explicit.is_file():
            raise BuildError(f"--iscc {explicit} does not exist")
        return explicit
    inno_dir = work / "inno-setup" / pins["inno_setup"].version
    iscc = inno_dir / "ISCC.exe"
    if iscc.is_file():
        return iscc
    installer = fetch(pins["inno_setup"], downloads)
    unzipped = work / "innounp"
    unzipped.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(fetch(pins["innounp"], downloads)) as zf:
        zf.extractall(unzipped)
    raw = work / "inno-setup" / "raw"
    if raw.exists():
        shutil.rmtree(raw)
    raw.mkdir(parents=True)
    run([unzipped / "innounp.exe", "-x", "-y", f"-d{raw}", installer])
    shutil.move(str(raw / "{app}"), str(inno_dir))
    if not iscc.is_file():
        raise BuildError(f"unpacking Inno Setup did not produce {iscc}")
    return iscc


def compile_installer(iscc: Path, stage: Path, work: Path, out_dir: Path, version: str) -> Path:
    from zones import render_pascal

    out_dir.mkdir(parents=True, exist_ok=True)
    zones_inc = work / "windows_zones.inc"
    zones_inc.write_text(render_pascal(), encoding="utf-8")
    run(
        [
            iscc,
            "/Qp",
            f"/DAppVersion={version}",
            f"/DAppVersionNumeric={numeric_version(version)}",
            f"/DStageDir={stage}",
            f"/DRepoRoot={REPO}",
            f"/DOutputDir={out_dir}",
            f"/I{zones_inc.parent}",  # where il2ks.iss finds windows_zones.inc
            HERE / "il2ks.iss",
        ]
    )
    exe = out_dir / f"il2ks-setup-{version}.exe"
    if not exe.is_file():
        raise BuildError(f"ISCC finished but {exe} is missing")
    return exe


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the il2ks Windows installer (dist/il2ks-setup-<version>.exe).")
    parser.add_argument(
        "--work", type=Path, default=REPO / "build" / "windows", help="work folder (default build/windows)"
    )
    parser.add_argument("--out", type=Path, default=REPO / "dist", help="output folder (default dist)")
    parser.add_argument(
        "--iscc", type=Path, default=None, help="ISCC.exe to use (default: the pinned Inno Setup, unpacked)"
    )
    parser.add_argument(
        "--stage-only", action="store_true", help="assemble and test the bundle, but do not compile the installer"
    )
    parser.add_argument("--keep-stage", action="store_true", help="reuse an existing work/stage (skip assembling)")
    args = parser.parse_args(argv)

    if sys.platform != "win32":
        print("The installer can only be built on Windows.", file=sys.stderr)
        return 2
    try:
        uv = shutil.which("uv")
        if uv is None:
            raise BuildError("uv was not found on the PATH (https://docs.astral.sh/uv/)")
        pins = parse_pins(PINS_FILE.read_text(encoding="utf-8"))
        version = read_version()
        work: Path = args.work.resolve()
        if len(str(work)) > MAX_WORK_PATH:
            raise BuildError(
                f"the work folder path is too long ({len(str(work))} characters): Inno Setup cannot open files whose "
                "full path exceeds 260 characters, and the bundle has paths 130 characters deep. "
                "Use a shorter --work, for example C:\\il2w."
            )
        downloads = work / "downloads"
        stage = work / "stage"
        if not (args.keep_stage and stage.is_dir()):
            wheel_dir = work / "wheel"
            if wheel_dir.exists():
                shutil.rmtree(wheel_dir)
            run([uv, "build", "--wheel", "--out-dir", wheel_dir], cwd=REPO)
            wheels = sorted(wheel_dir.glob("il2ks-*.whl"))
            if len(wheels) != 1:
                raise BuildError(f"expected one il2ks wheel in {wheel_dir}, found {len(wheels)}")
            requirements = work / "requirements.txt"
            run(
                [
                    uv,
                    "export",
                    "--frozen",
                    "--no-dev",
                    "--no-emit-project",
                    "--format",
                    "requirements-txt",
                    "-o",
                    requirements,
                ],
                cwd=REPO,
                quiet=True,
            )
            stage = build_stage(work, pins, downloads, wheels[0], requirements, uv)
        print(f"bundle ready: {stage}")
        if args.stage_only:
            return 0
        iscc = ensure_iscc(work, pins, downloads, args.iscc)
        exe = compile_installer(iscc, stage, work, args.out.resolve(), version)
        print(f"\nbuilt {exe} ({exe.stat().st_size / 1_000_000:.1f} MB), SHA-256 {sha256_of(exe)}")
    except BuildError as exc:
        print(f"build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
