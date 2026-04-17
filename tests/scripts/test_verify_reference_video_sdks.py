import pytest

from scripts.verify_reference_video_sdks import Provider, parse_args


def test_parse_args_provider_required():
    with pytest.raises(SystemExit):
        parse_args([])


def test_parse_args_provider_accepts_all_four():
    for name in ("ark", "grok", "veo", "sora"):
        args = parse_args(["--provider", name])
        assert args.provider == Provider(name)


def test_parse_args_rejects_unknown_provider():
    with pytest.raises(SystemExit):
        parse_args(["--provider", "unknown"])


def test_parse_args_defaults():
    args = parse_args(["--provider", "ark"])
    assert args.refs == 3
    assert args.duration == 5
    assert args.multi_shot is False
    assert args.report_dir.name == "verification-reports"


def test_parse_args_override():
    args = parse_args(
        [
            "--provider",
            "grok",
            "--refs",
            "7",
            "--duration",
            "10",
            "--multi-shot",
        ]
    )
    assert args.refs == 7
    assert args.duration == 10
    assert args.multi_shot is True
