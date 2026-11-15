"""Build a self-contained Windows KiCad 10 PCM ZIP; never load KiCad or install.

Run with ordinary Python. Supply a vendor directory of cp311-win_amd64 wheels
already expanded by pip, or reuse the previous manual installation's directory.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import struct
import zipfile

ROOT = Path(__file__).resolve().parent
VERSIONS = {"numpy": "2.4.6", "shapely": "2.1.2", "svgelements": "1.9.6"}


def default_vendor():
    identifier = json.loads((ROOT/"metadata.json").read_text())["identifier"]
    pcm = Path.home()/"Documents/KiCad/10.0/3rdparty/plugins"
    for name in (identifier.replace(".", "_"), "com_jerrymk_silk-overlay-composer"):
        candidate = pcm/name/"vendor"
        if candidate.is_dir():
            return candidate
    installed = Path.home()/"Documents/KiCad/10.0/scripting/plugins/silk_overlay"
    pointer = installed/"dependencies.txt"
    return installed/pointer.read_text().strip() if pointer.is_file() else installed/"vendor"


def collect(vendor):
    files = {}
    for name, version in VERSIONS.items():
        info = vendor/f"{name}-{version}.dist-info"
        wheel = (info/"WHEEL").read_text()
        if name != "svgelements" and "Tag: cp311-cp311-win_amd64" not in wheel:
            raise ValueError(f"Wrong Python/platform wheel for {name}; expected cp311-win_amd64")
        for directory in (vendor/name, vendor/(name+".libs"), info):
            if not directory.exists():
                continue
            for path in sorted(directory.rglob("*")):
                if (path.is_file() and not {"__pycache__", "tests"}.intersection(path.parts)
                        and path.suffix.lower() not in (".pyc", ".pyo", ".tmp", ".bak")
                        and not path.name.endswith("~")):
                    files["plugins/vendor/"+path.relative_to(vendor).as_posix()] = path.read_bytes()
    for path in sorted((ROOT/"silk_overlay").glob("*.py")):
        ast.parse(path.read_text(encoding="utf-8"))
        files["plugins/"+path.name] = path.read_bytes()
    for name in ("icon.png", "icon-update.png"):
        files["plugins/"+name] = (ROOT/"silk_overlay"/name).read_bytes()
    files["resources/icon.png"] = (ROOT/"silk_overlay/icon-64.png").read_bytes()
    for name in ("README.md", "LICENSE.txt", "THIRD_PARTY.md", "RELEASE_NOTES.md"):
        files["plugins/"+name] = (ROOT/name).read_bytes()
    return files


def source_files(prefix):
    """Allowlist public source; omit local boards, tests and development notes."""
    paths = [ROOT/name for name in ("README.md", "LICENSE.txt", "THIRD_PARTY.md", "RELEASE_NOTES.md",
                                   "metadata.json", "build_pcm.py", "requirements.txt", ".gitignore")]
    paths.extend(sorted((ROOT/"silk_overlay").glob("*.py")))
    paths.extend(sorted((ROOT/"silk_overlay").glob("*.png")))
    return {prefix+"/"+path.relative_to(ROOT).as_posix(): path.read_bytes() for path in paths}


def write_archive(path, files):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 11, 15, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix(".zip.sha256").write_text(f"{digest}  {path.name}\n")
    return digest


def validate_archive(path, metadata):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names))
        assert archive.testzip() is None
        assert {"metadata.json", "plugins/__init__.py", "plugins/action.py", "resources/icon.png"}.issubset(names)
        assert all(name.split('/')[0] in ("metadata.json", "plugins", "resources") and ".." not in name.split('/') for name in names)
        assert not any("__pycache__" in name or name.endswith((".kicad_pcb", ".pyc")) for name in names)
        assert not any(name.lower().endswith((".tmp", ".bak", "~")) for name in names)
        assert "plugins/install.py" not in names and "plugins/dependencies.txt" not in names
        assert json.loads(archive.read("metadata.json")) == metadata
        assert struct.unpack(">II", archive.read("resources/icon.png")[16:24]) == (64, 64)
        for name in ("icon.png", "icon-update.png"):
            assert struct.unpack(">II", archive.read("plugins/"+name)[16:24]) == (32, 32)
        for package in VERSIONS:
            assert f"plugins/vendor/{package}/__init__.py" in names
        assert any(name.endswith('.pyd') and '/shapely/' in name for name in names)
        assert any(name.endswith('.dll') and '/shapely.libs/' in name for name in names)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--vendor", type=Path, default=default_vendor())
    parser.add_argument("--output", type=Path, default=ROOT/"dist")
    parser.add_argument("--schema", type=Path, help="Optional installed KiCad PCM schema; needs jsonschema")
    parser.add_argument("--source", action="store_true", help="Also build the public source ZIP")
    args = parser.parse_args()
    files = collect(args.vendor)
    metadata = json.loads((ROOT/"metadata.json").read_text())
    metadata["versions"][0]["install_size"] = sum(map(len, files.values()))
    if args.schema:
        import jsonschema
        jsonschema.validate(metadata, json.loads(args.schema.read_text()))
    files["metadata.json"] = (json.dumps(metadata, indent=2)+"\n").encode()
    args.output.mkdir(parents=True, exist_ok=True)
    version = metadata["versions"][0]["version"]
    prefix = metadata["name"].replace(" ", "")+"-"+version
    path = args.output/f"{prefix}-windows-kicad10-pcm.zip"
    digest = write_archive(path, files)
    validate_archive(path, metadata)
    print(f"Validated PCM package: {path}\n{path.stat().st_size:,} bytes; SHA256 {digest}")
    if args.source:
        source = args.output/f"{prefix}-source.zip"
        source_digest = write_archive(source, source_files(prefix))
        with zipfile.ZipFile(source) as archive:
            assert archive.testzip() is None
        print(f"Source package: {source}\n{source.stat().st_size:,} bytes; SHA256 {source_digest}")


if __name__ == "__main__":
    main()
