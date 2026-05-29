import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("worktree_name", ["predict-rlm.feature", "predict-rlm-feature"])
def test_setup_hook_links_env_development_from_sibling_worktree(tmp_path, worktree_name):
    main_repo = tmp_path / "predict-rlm"
    worktree = tmp_path / worktree_name
    main_repo.mkdir()
    worktree.mkdir()
    (main_repo / ".env.development").write_text("TOKEN=dev\n")

    hook = Path(__file__).resolve().parents[1] / ".wt" / "hooks" / "setup.sh"

    subprocess.run(
        [str(hook), str(worktree), worktree_name],
        check=True,
        text=True,
        capture_output=True,
    )

    env_link = worktree / ".env.development"
    assert env_link.is_symlink()
    assert os.readlink(env_link) == "../predict-rlm/.env.development"
    assert env_link.resolve() == main_repo / ".env.development"
