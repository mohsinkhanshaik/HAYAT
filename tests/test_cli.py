import io

import main


def test_main_accepts_unicode_safe_streams(monkeypatch):
    class Stream(io.StringIO):
        def reconfigure(self, **kwargs):
            self.configuration = kwargs

    stdout, stderr = Stream(), Stream()
    monkeypatch.setattr(main.sys, "stdout", stdout)
    monkeypatch.setattr(main.sys, "stderr", stderr)
    monkeypatch.setattr(main.sys, "argv", ["main.py", "--help"])
    try:
        main.main()
    except SystemExit:
        pass
    assert stdout.configuration["encoding"] == "utf-8"
