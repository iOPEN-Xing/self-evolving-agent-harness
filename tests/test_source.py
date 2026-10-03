import pytest

from versioning.source import fingerprint, unchanged


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "examples/23-final-assembly"
    root.mkdir(parents=True)
    entry = root / "main.py"
    entry.write_text("print('frozen')")
    return tmp_path, root, entry


@pytest.mark.parametrize("mutation", ["add", "delete", "change", "json"])
def test_complete_inventory_is_frozen(tree, mutation):
    repo, root, entry = tree
    manifest = fingerprint(repo, entry)
    assert unchanged(manifest, repo)
    if mutation == "add":
        (root / "plugin.py").write_text("print('new code')")
    elif mutation == "json":
        (root / "config.json").write_text('{"budget":999}')
    elif mutation == "delete":
        entry.unlink()
    else:
        entry.write_text("print('modified')")
    assert not unchanged(manifest, repo)


def test_dependency_addition_detected_and_outputs_excluded(tree):
    repo, root, entry = tree
    vendor = repo / ".deps/vendor"
    vendor.mkdir(parents=True)
    (vendor / "engine.py").write_text("# engine")
    manifest = fingerprint(repo, entry, [vendor])
    (root / "output").mkdir()
    (root / "output/run.json").write_text("{}")
    assert unchanged(manifest, repo)
    (vendor / "new_engine.py").write_text("# new engine")
    assert not unchanged(manifest, repo)


def test_invalid_or_legacy_manifest_fails_closed(tree):
    repo, _, entry = tree
    for manifest in ({}, {"files": {}}, {"files": {str(entry): "bad"}}):
        assert unchanged(manifest, repo) is False


def test_symlink_source_rejected(tree):
    repo, root, entry = tree
    manifest = fingerprint(repo, entry)
    (root / "plugin.py").symlink_to(entry)
    assert not unchanged(manifest, repo)
