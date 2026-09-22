"""Incident: the Windows scheduled task kept publishing to the channel next to the cloud pipeline — the same
story twice on 22 Sep, a third video on 19 Sep. A local run must not publish unless told to."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from run_pipeline import local_publish_blocked  # noqa: E402


def test_a_local_publishing_run_is_blocked():
    assert local_publish_blocked(True, False, env={})
    assert local_publish_blocked(None, True, env={})              # ENABLE_PUBLISH=true is the default publish switch


def test_github_actions_and_an_explicit_opt_in_may_publish():
    assert not local_publish_blocked(True, True, env={"GITHUB_ACTIONS": "true"})
    assert not local_publish_blocked(True, True, env={"ALLOW_LOCAL_PUBLISH": "1"})


def test_a_build_only_run_is_never_blocked():
    assert not local_publish_blocked(False, True, env={})
    assert not local_publish_blocked(None, False, env={})
