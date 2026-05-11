def test_import_autograde_idp() -> None:
    import autograde_idp

    assert hasattr(autograde_idp, "__version__")


def test_cli_main_exits_zero() -> None:
    from autograde_idp.cli import main

    assert main([]) == 0


def test_cli_version_flag(capsys) -> None:
    import sys

    from autograde_idp import __version__
    from autograde_idp.cli import main

    rc = main(["--version"])
    out = capsys.readouterr().out
    assert rc == 0
    assert __version__ in out
    assert sys.platform in out


def test_cli_version_subcommand(capsys) -> None:
    from autograde_idp.cli import main

    rc = main(["version"])
    assert rc == 0
    assert "autograde" in capsys.readouterr().out


def test_cli_whoami_handles_corrupted_token(monkeypatch, tmp_path, capsys) -> None:
    from autograde_idp import auth
    from autograde_idp.cli import main

    target = tmp_path / "token.json"
    target.write_text('{"access_token": "only-this"}', encoding="utf-8")
    monkeypatch.setattr(auth, "token_path", lambda: target)

    rc = main(["whoami"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "token" in err.lower()
