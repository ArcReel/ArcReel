import re
import tomllib
from pathlib import Path
from xml.etree import ElementTree

import pytest
import yaml
from coverage import CoverageData

from scripts.nightly_coverage import PACKAGE_SUITES, combine

_REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("missing", [None, 1, 2])
def test_report_requires_each_shards_lines(tmp_path, monkeypatch, missing):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COVERAGE_FILE", raising=False)
    source = tmp_path / "subject.py"
    source.write_text("x = 1\ny = 2\n", encoding="utf-8")
    for shard in (1, 2):
        if shard != missing:
            directory = Path(f"coverage-{shard}")
            directory.mkdir()
            data = CoverageData(basename=str(directory / "raw.coverage"))
            data.add_lines({str(source): {shard}})
            data.write()
    if missing:
        with pytest.raises(ValueError, match=f"missing coverage shard: coverage-{missing}/raw.coverage"):
            combine(Path("."), 2)
        assert not Path("coverage-backend.xml").exists()
    else:
        combine(Path("."), 2)
        lines = ElementTree.parse("coverage-backend.xml").findall(".//class/lines/line")
        assert [(line.get("number"), line.get("hits")) for line in lines] == [("1", "1"), ("2", "1")]


def test_combine_rejects_empty_and_unexpected_shards(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COVERAGE_FILE", raising=False)
    source = tmp_path / "subject.py"
    source.write_text("x = 1\n", encoding="utf-8")

    (tmp_path / "coverage-1").mkdir()
    empty = CoverageData(basename=str(tmp_path / "coverage-1" / "raw.coverage"))
    empty.add_lines({})
    empty.write()
    with pytest.raises(ValueError, match=re.escape("empty coverage shard: coverage-1/raw.coverage")):
        combine(Path("."), 1)

    valid = CoverageData(basename=str(tmp_path / "coverage-1" / "raw.coverage"))
    valid.add_lines({str(source): {1}})
    valid.write()
    (tmp_path / "coverage-2").mkdir()
    extra = CoverageData(basename=str(tmp_path / "coverage-2" / "raw.coverage"))
    extra.add_lines({str(source): {1}})
    extra.write()
    with pytest.raises(ValueError, match="unexpected coverage shards"):
        combine(Path("."), 1)


def test_nightly_coverage_config_matches_workspace_and_workflow():
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    expected_suites = [Path(member) / "tests" for member in pyproject["tool"]["uv"]["workspace"]["members"]]
    assert expected_suites == PACKAGE_SUITES

    workflow = yaml.safe_load((_REPO_ROOT / ".github" / "workflows" / "nightly-full.yml").read_text(encoding="utf-8"))
    shards = workflow["jobs"]["backend-coverage"]["strategy"]["matrix"]["shard"]
    combine_step = next(
        step
        for step in workflow["jobs"]["backend-report"]["steps"]
        if step.get("name") == "Verify shards and combine once"
    )
    match = re.search(r"--total\s+(\d+)", combine_step["run"])
    assert match is not None
    assert int(match[1]) == len(shards)
