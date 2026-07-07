"""Tests for the workspace path-traversal guard used by every filesystem/shell/code tool."""
import pytest

from app.workspace.manager import resolve_in_workspace, workspace_dir


def test_resolve_in_workspace_allows_nested_path():
    conv_id = "test-conv-nested"
    p = resolve_in_workspace(conv_id, "sub/dir/file.txt")
    # .resolve() on both sides: on macOS workspace_dir() may return a path through a
    # symlink (e.g. /var -> /private/var) that only resolve_in_workspace canonicalizes.
    assert p == (workspace_dir(conv_id) / "sub" / "dir" / "file.txt").resolve()


def test_resolve_in_workspace_allows_dot():
    conv_id = "test-conv-dot"
    assert resolve_in_workspace(conv_id, ".") == workspace_dir(conv_id).resolve()


@pytest.mark.parametrize(
    "escape_path",
    [
        "../escape.txt",
        "../../etc/passwd",
        "sub/../../escape.txt",
        "..\\escape.txt",  # Windows-style traversal must be rejected on every host
        "/etc/passwd",
    ],
)
def test_resolve_in_workspace_rejects_traversal(escape_path):
    with pytest.raises(ValueError, match="escapes workspace"):
        resolve_in_workspace("test-conv-escape", escape_path)
