from __future__ import annotations

from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from app.video_results import VideoResultServer


@pytest.fixture
def shared(tmp_path):
    path = tmp_path / 'video.mp4'
    path.write_bytes(b'0123456789')
    server = VideoResultServer()
    url = server.share(path, '<script>title</script>', '<img src=x onerror=alert(1)>')
    try:
        yield server, url, path
    finally:
        server.close()


def test_result_escapes_text_and_streams_seeking_ranges(shared):
    _, url, _ = shared
    with urlopen(url, timeout=3) as response:
        body = response.read().decode()
        assert '<video controls' in body
        assert '&lt;script&gt;title&lt;/script&gt;' in body
        assert '<img src=x' not in body
        assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']
    for interval, expected in [('bytes=3-6', b'3456'), ('bytes=-3', b'789'), ('bytes=7-', b'789')]:
        with urlopen(Request(url + '/video', headers={'Range': interval}), timeout=3) as response:
            assert response.status == 206
            assert response.read() == expected
            assert response.headers['Accept-Ranges'] == 'bytes'
    with urlopen(url + '/video?download=1', timeout=3) as response:
        assert 'attachment' in response.headers['Content-Disposition']
        assert response.read() == b'0123456789'


@pytest.mark.parametrize('interval', ['bytes=999-', 'bytes=3-1', 'bytes=-0', 'bytes=0-1,3-4', 'bytes=-'])
def test_invalid_ranges_never_send_the_whole_file(shared, interval):
    _, url, _ = shared
    with pytest.raises(HTTPError) as result:
        urlopen(Request(url + '/video', headers={'Range': interval}), timeout=3)
    assert result.value.code == 416
    assert result.value.headers['Content-Range'] == 'bytes */10'


def test_local_link_refuses_wrong_host_unknown_paths_and_changed_files(shared):
    _, url, path = shared
    for request, code in [(Request(url, headers={'Host': 'evil.example'}), 403),
                          (Request(url + '/../../secret'), 404),
                          (Request(url + '/nope'), 404)]:
        with pytest.raises(HTTPError) as result:
            urlopen(request, timeout=3)
        assert result.value.code == code
    path.write_bytes(b'changed video')
    with pytest.raises(HTTPError) as result:
        urlopen(url + '/video', timeout=3)
    assert result.value.code == 410
