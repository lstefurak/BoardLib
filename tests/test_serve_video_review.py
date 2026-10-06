import importlib.util
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


SPEC = importlib.util.spec_from_file_location("serve_video_review", Path(__file__).parents[1] / "tools" / "serve_video_review.py")
server_tool = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(server_tool)


@pytest.fixture
def server(tmp_path):
    (tmp_path / "review.html").write_text("Gallery", encoding="utf-8")
    (tmp_path / "clip.mp4").write_bytes(b"0123456789")
    (tmp_path / ".private").write_text("private")
    (tmp_path / "private.json").write_text('{"private": true}')
    (tmp_path / "notes.txt").write_text("private")
    (tmp_path / "other.html").write_text("private")
    (tmp_path / "directory").mkdir()
    instance = server_tool.make_server(tmp_path, 0)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{instance.server_port}", tmp_path
    instance.shutdown()
    instance.server_close()
    thread.join()


@pytest.mark.parametrize("range_header,expected,content_range", [
    ("bytes=2-5", b"2345", "bytes 2-5/10"),
    ("bytes=6-", b"6789", "bytes 6-9/10"),
    ("bytes=-3", b"789", "bytes 7-9/10"),
    ("bytes=2-100", b"23456789", "bytes 2-9/10"),
])
def test_video_byte_ranges_support_phone_seeking(server, range_header, expected, content_range):
    base, _ = server
    with urlopen(Request(base + "/clip.mp4", headers={"Range": range_header})) as response:
        assert response.status == 206
        assert response.headers["Content-Range"] == content_range
        assert int(response.headers["Content-Length"]) == len(expected)
        assert response.read() == expected
        assert response.headers["Cache-Control"] == "no-cache"


@pytest.mark.parametrize("range_header", ["bytes=100-", "bytes=5-2", "bytes=-0", "bytes=-", "bytes=1-2,4-5", "invalid"])
def test_invalid_ranges_are_rejected(server, range_header):
    base, _ = server
    with pytest.raises(HTTPError) as failure:
        urlopen(Request(base + "/clip.mp4", headers={"Range": range_header}))
    assert failure.value.code == 416
    assert failure.value.headers["Content-Range"] == "bytes */10"


def test_root_gallery_head_and_private_paths(server):
    base, _ = server
    with urlopen(base + "/") as response:
        assert response.read() == b"Gallery"
    with urlopen(Request(base + "/clip.mp4", method="HEAD", headers={"Range": "bytes=2-5"})) as response:
        assert response.status == 206 and response.read() == b""
    for path in ("/.private", "/directory/", "/../secret.mp4", "/private.json", "/notes.txt", "/other.html"):
        with pytest.raises(HTTPError) as failure:
            urlopen(base + path)
        assert failure.value.code == 403


def test_symlink_outside_root_is_not_served(server):
    base, root = server
    secret = root.parent / "secret.txt"
    secret.write_text("private")
    try:
        (root / "linked.txt").symlink_to(secret)
    except OSError:
        pytest.skip("symlink creation requires privileges on this platform")
    with pytest.raises(HTTPError) as failure:
        urlopen(base + "/linked.txt")
    assert failure.value.code == 403
