"""The maintenance command cannot delete tracked files or application state."""

from scripts.clean_workspace import cleanup_targets


def test_cleanup_preserves_tracked_files_and_business_data(tmp_path):
    for name in ("build", ".pytest_cache", "reports", ".dev-logs", ".dev-pids",
                 ".venv", ".local-artifacts", "dist", "src-tauri/target/release", "frontend/dist"):
        target = tmp_path / name
        target.mkdir(parents=True)
        (target / "keep").write_text("data")
    targets = cleanup_targets(tmp_path, {"build/keep"})
    assert targets == [tmp_path / ".pytest_cache"]


def test_cleanup_does_not_follow_symlink_or_its_ancestors(tmp_path):
    outside = tmp_path / "outside"
    (outside / "target/debug").mkdir(parents=True)
    (tmp_path / "src-tauri").symlink_to(outside, target_is_directory=True)
    (tmp_path / "build").symlink_to(outside, target_is_directory=True)
    source = tmp_path / "tradingagents"
    source.mkdir()
    (source / "external").symlink_to(outside, target_is_directory=True)
    assert cleanup_targets(tmp_path, set()) == []


def test_cleanup_lists_source_bytecode_and_build_cache_only_once(tmp_path):
    nested = tmp_path / "tradingagents/__pycache__/nested/__pycache__"
    nested.mkdir(parents=True)
    (tmp_path / ".DS_Store").write_text("metadata")
    debug = tmp_path / "src-tauri/target/debug"
    debug.mkdir(parents=True)
    targets = cleanup_targets(tmp_path, set())
    assert set(targets) == {tmp_path / ".DS_Store", debug, tmp_path / "tradingagents/__pycache__"}
