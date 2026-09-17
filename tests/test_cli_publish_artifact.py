"""`publish-artifact` — the human-approval publish path: takes an ALREADY-
BUILT video + its meta.json exactly as reviewed, never rebuilds it (a fresh
build could produce different content than what a person actually approved).
Part of the held-video review flow — see .claude/agents/held-review.md."""
import json

from political_shorts import cli
from political_shorts.publishers.base import PublishResult


class _FakePub:
    def __init__(self, result: PublishResult):
        self._result = result
        self.calls: list[tuple] = []

    def publish(self, video_path, meta):
        self.calls.append((video_path, meta))
        return self._result


def _args(video, meta):
    ns = type("Args", (), {})()
    ns.video = str(video)
    ns.meta = str(meta)
    return ns


def _write_pair(tmp_path):
    video = tmp_path / "short.mp4"
    video.write_bytes(b"fake mp4 bytes")
    meta = tmp_path / "short.meta.json"
    meta.write_text(json.dumps({"headline": "테스트 스토리", "title": ["1줄", "2줄"]}),
                    encoding="utf-8")
    return video, meta


def test_publishes_the_exact_given_file_with_no_rebuild(monkeypatch, tmp_path):
    video, meta = _write_pair(tmp_path)
    fake = _FakePub(PublishResult("youtube", "ok", False, remote_id="abc123",
                                  url="https://youtube.com/shorts/abc123"))
    # cmd_publish_artifact does `from .publishers import get_publishers`
    # locally, so patching the source module is what the local import picks up
    import political_shorts.publishers as pubmod
    monkeypatch.setattr(pubmod, "get_publishers", lambda cfg=None: [fake])

    rc = cli.cmd_publish_artifact(_args(video, meta))
    assert rc == 0
    assert len(fake.calls) == 1
    called_video, called_meta = fake.calls[0]
    assert str(called_video) == str(video)
    assert called_meta["headline"] == "테스트 스토리"


def test_missing_video_is_a_clean_error_not_a_crash(tmp_path):
    _, meta = _write_pair(tmp_path)
    rc = cli.cmd_publish_artifact(_args(tmp_path / "nope.mp4", meta))
    assert rc == 2


def test_missing_meta_is_a_clean_error_not_a_crash(tmp_path):
    video, _ = _write_pair(tmp_path)
    rc = cli.cmd_publish_artifact(_args(video, tmp_path / "nope.meta.json"))
    assert rc == 2


def test_returns_nonzero_when_every_publisher_fails(monkeypatch, tmp_path):
    video, meta = _write_pair(tmp_path)
    fake = _FakePub(PublishResult("youtube", "error", False, detail="boom"))
    import political_shorts.publishers as pubmod
    monkeypatch.setattr(pubmod, "get_publishers", lambda cfg=None: [fake])

    rc = cli.cmd_publish_artifact(_args(video, meta))
    assert rc == 1
