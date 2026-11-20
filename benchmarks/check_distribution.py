"""Check release archives without extracting or importing their contents."""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import zipfile


def check_paths(names, *, source):
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
            raise ValueError("Archive contains an unsafe path")
        parts = path.parts[1:] if source else path.parts
        if not parts:
            continue
        if parts[0] in {"results", "data", "AgentTelemetry", ".git", ".venv"}:
            raise ValueError("Archive contains local-only data")
        if any(part == ".env" or (part.startswith(".env.") and part != ".env.example") for part in parts):
            raise ValueError("Archive contains environment files")
        if parts[-1].endswith("_key.pem") and tuple(parts) != ("tests", "fixtures", "trace_desk_test_key.pem"):
            raise ValueError("Archive contains an unexpected private-key file")


def inspect_archive(path):
    source = path.name.endswith(".tar.gz")
    if source:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            if any(not (member.isfile() or member.isdir()) for member in members):
                raise ValueError("Source archive contains links or special files")
            names = [member.name for member in members]
            check_paths(names, source=True)
            roots = {PurePosixPath(name).parts[0] for name in names}
            if len(roots) != 1:
                raise ValueError("Source archive must have a single root")
            root = next(iter(roots))
            required = {"pyproject.toml", "README.md", "LICENSE", "CHANGELOG.md", "SECURITY.md",
                        "src/chrolo/cli.py", "examples/pilot_ticket_agent.py", "benchmarks/real_regressions.json"}
            if not all(root + "/" + name in names for name in required):
                raise ValueError("Source archive is missing release files")
            stream = archive.extractfile(root + "/PKG-INFO")
            if stream is None:
                raise ValueError("Source archive has no package metadata")
            metadata = BytesParser().parsebytes(stream.read())
    else:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            check_paths(names, source=False)
            candidates = [name for name in names if name.endswith(".dist-info/METADATA")]
            if len(candidates) != 1:
                raise ValueError("Wheel must have exactly one package metadata file")
            prefix = candidates[0].rsplit("/", 1)[0]
            if "chrolo/cli.py" not in names or prefix + "/licenses/LICENSE" not in names:
                raise ValueError("Wheel is missing runtime or license files")
            metadata = BytesParser().parsebytes(archive.read(candidates[0]))
    if metadata["Name"] != "chrolo-starter" or metadata["License-Expression"] != "MIT":
        raise ValueError("Unexpected distribution identity or license")
    if metadata["Requires-Python"] != ">=3.10":
        raise ValueError("Unexpected Python requirement")
    return {"filename": path.name, "version": metadata["Version"], "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "kind": "sdist" if source else "wheel"}


def check_distributions(directory, *, expected_version=None, require_prerelease=False):
    wheels = list(directory.glob("*.whl"))
    sources = list(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise ValueError("Use a clean directory containing exactly one wheel and one sdist")
    artifacts = [inspect_archive(path) for path in wheels + sources]
    versions = {artifact["version"] for artifact in artifacts}
    if len(versions) != 1 or (expected_version is not None and versions != {expected_version}):
        raise ValueError("Distribution versions differ from each other or the expected release")
    version = next(iter(versions))
    if require_prerelease and not re.fullmatch(r"\d+\.\d+\.\d+(?:a|b|rc)\d+", version):
        raise ValueError("Expected an explicitly marked prerelease version")
    return {"format": "chrolo.distribution-check.v1", "passed": True, "complete": True,
            "version": version, "artifacts": artifacts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", required=True, type=Path)
    parser.add_argument("--expected-version")
    parser.add_argument("--require-prerelease", action="store_true")
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    protected = [*args.dist_dir.glob("*.whl"), *args.dist_dir.glob("*.tar.gz")]
    if args.report.resolve() in [path.resolve() for path in protected]:
        parser.error("Report must differ from distribution archives")
    args.report.write_text(json.dumps({"passed": False, "complete": False}) + "\n", encoding="utf-8")
    report = check_distributions(args.dist_dir, expected_version=args.expected_version, require_prerelease=args.require_prerelease)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Distribution checks passed for", report["version"])


if __name__ == "__main__":
    main()
