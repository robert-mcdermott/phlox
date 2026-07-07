"""Concurrent checkpoint creation must not corrupt the workspace's git repo.

Regression test for the per-workspace lock added to app.workspace.checkpoints: before it,
two threads calling create_checkpoint at the same time (e.g. two sub-agents mutating files
in parallel) raced on `git add -A && git commit` against the same index/lock file.
"""
import threading

from app.workspace import checkpoints


def test_concurrent_create_checkpoint_does_not_corrupt_repo(tmp_path):
    if not checkpoints.git_available():
        return  # environment has no git; nothing to test

    workspace = tmp_path / "ws"
    workspace.mkdir()
    checkpoints.ensure_repo(workspace)

    errors: list[Exception] = []

    def worker(n: int) -> None:
        try:
            (workspace / f"file{n}.txt").write_text(f"content {n}")
            checkpoints.create_checkpoint(workspace, f"from thread {n}")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors
    assert not any(t.is_alive() for t in threads)

    # The repo is intact and every file made it into some checkpoint.
    rows = checkpoints.list_checkpoints(workspace)
    assert len(rows) >= 1
    for n in range(8):
        assert (workspace / f"file{n}.txt").exists()


def test_restore_checkpoint_is_reentrant_with_its_own_lock(tmp_path):
    # restore_checkpoint acquires the workspace lock, then calls create_checkpoint
    # (twice) while still holding it — must not deadlock (requires an RLock).
    workspace = tmp_path / "ws2"
    workspace.mkdir()
    if not checkpoints.ensure_repo(workspace):
        return

    (workspace / "a.txt").write_text("v1")
    sha = checkpoints.create_checkpoint(workspace, "v1")
    assert sha

    (workspace / "a.txt").write_text("v2")
    checkpoints.create_checkpoint(workspace, "v2")

    ok = checkpoints.restore_checkpoint(workspace, sha)
    assert ok
    assert (workspace / "a.txt").read_text() == "v1"
