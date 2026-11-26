from pathlib import Path
from xml.etree import ElementTree

import pytest
from coverage import CoverageData

from scripts.nightly_coverage import combine


@pytest.mark.parametrize("missing", [None, 1, 2])
def test_report_requires_each_shards_lines(tmp_path, monkeypatch, missing):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COVERAGE_FILE", raising=False)
    source = tmp_path / "subject.py"
    source.write_text("x = 1\ny = 2\n")
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
