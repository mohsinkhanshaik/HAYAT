import main


def test_scheduler_daemon_flag_is_documented(monkeypatch, capsys):
    monkeypatch.setattr(main.sys, "argv", ["main.py", "--help"])
    try:
        main.main()
    except SystemExit:
        pass
    assert "--scheduler-daemon" in capsys.readouterr().out
