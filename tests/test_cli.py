import pytest

from screener.cli import build_parser, main


def test_help_when_no_command(capsys):
    assert main([]) == 2
    assert "screener" in capsys.readouterr().out


def test_subcommands_are_registered():
    parser = build_parser()
    args = parser.parse_args(["detect", "--date", "2026-07-10"])
    assert args.command == "detect"
    assert args.date == "2026-07-10"


def test_tune_requires_sector_or_all():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["tune"])


def test_unimplemented_command_returns_nonzero():
    assert main(["fetch"]) == 1
