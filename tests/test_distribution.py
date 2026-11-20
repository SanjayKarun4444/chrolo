from io import BytesIO
import tarfile
import zipfile

import pytest

from benchmarks.check_distribution import check_distributions, check_paths, inspect_archive


def artifacts(tmp_path, *, version="0.2.0a1", source_version=None):
    def metadata(value):
        return f"Metadata-Version: 2.4\nName: chrolo-starter\nVersion: {value}\nRequires-Python: >=3.10\nLicense-Expression: MIT\n\n".encode()
    wheel = tmp_path / "candidate.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("chrolo/cli.py", "")
        archive.writestr("candidate.dist-info/licenses/LICENSE", "MIT")
        archive.writestr("candidate.dist-info/METADATA", metadata(version))
    source = tmp_path / "candidate.tar.gz"
    with tarfile.open(source, "w:gz") as archive:
        names = ["pyproject.toml", "README.md", "LICENSE", "CHANGELOG.md", "SECURITY.md", "src/chrolo/cli.py",
                 "examples/pilot_ticket_agent.py", "benchmarks/real_regressions.json", "PKG-INFO"]
        for name in names:
            value = metadata(source_version or version) if name == "PKG-INFO" else b"fixture"
            info = tarfile.TarInfo("candidate/" + name)
            info.size = len(value)
            archive.addfile(info, BytesIO(value))
    return wheel, source


def test_matching_alpha_artifacts_are_validated_together(tmp_path):
    artifacts(tmp_path)
    report = check_distributions(tmp_path, expected_version="0.2.0a1", require_prerelease=True)
    assert report["passed"] and {a["kind"] for a in report["artifacts"]} == {"wheel", "sdist"}


@pytest.mark.parametrize("path", ["../secret", "/absolute", "C:/secret", "folder\\secret", "results/trace.json", "data/private.json", ".env", "nested/.env.production", "deployment_key.pem"])
def test_unsafe_or_local_only_paths_are_rejected(path):
    for name, source in [(path, False), (path if path.startswith("/") else "root/" + path, True)]:
        with pytest.raises(ValueError):
            check_paths([name], source=source)


def test_documented_localhost_key_is_allowed_only_in_test_fixture_location():
    check_paths(["root/tests/fixtures/trace_desk_test_key.pem"], source=True)
    with pytest.raises(ValueError):
        check_paths(["root/deployment/trace_desk_test_key.pem"], source=True)


@pytest.mark.parametrize("options", [{"source_version": "0.2.0a2"}, {"version": "0.2.0"}])
def test_version_mismatch_and_unmarked_stable_release_are_rejected(tmp_path, options):
    artifacts(tmp_path, **options)
    with pytest.raises(ValueError):
        check_distributions(tmp_path, require_prerelease=True)


def test_source_archive_links_are_rejected(tmp_path):
    source = tmp_path / "linked.tar.gz"
    with tarfile.open(source, "w:gz") as archive:
        link = tarfile.TarInfo("root/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        archive.addfile(link)
    with pytest.raises(ValueError, match="links"):
        inspect_archive(source)


def test_stale_distribution_directory_is_rejected(tmp_path):
    wheel, _ = artifacts(tmp_path)
    (tmp_path / "older.whl").write_bytes(wheel.read_bytes())
    with pytest.raises(ValueError, match="exactly one"):
        check_distributions(tmp_path)
