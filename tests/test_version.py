"""The code fingerprint that keys cached upload results."""

import shutil

from funnel_magnet.version import PACKAGE_DIR, code_version


def package_copy(tmp_path):
    copy = tmp_path / "funnel_magnet"
    shutil.copytree(PACKAGE_DIR, copy, ignore=shutil.ignore_patterns("__pycache__"))
    return copy


def test_fingerprint_is_stable_for_unchanged_code():
    assert code_version() == code_version()


def test_fingerprint_changes_when_the_importer_changes(tmp_path):
    copy = package_copy(tmp_path)
    before = code_version(copy)

    importer = copy / "sources" / "meta.py"
    importer.write_text(importer.read_text("utf-8") + "# changed\n", "utf-8")

    assert code_version(copy) != before


def test_fingerprint_ignores_compiled_files(tmp_path):
    copy = package_copy(tmp_path)
    before = code_version(copy)
    (copy / "stray.pyc").write_bytes(bytes([0]))

    assert code_version(copy) == before
