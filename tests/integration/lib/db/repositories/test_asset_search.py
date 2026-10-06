"""AssetRepository 的名称搜索：方言敏感，PostgreSQL 与 SQLite 上同一套判据。

搜索词按字面子串匹配、不区分 ASCII 大小写；``%``、``_`` 与转义字符不作通配符。
"""

from __future__ import annotations

import pytest

from lib.db.repositories.asset_repo import AssetRepository

NAMES = ["Alice", "ALICE 战斗装", "alicia", "100% 纯度", "1000 纯度", "a_b", "axb", "路径/备份", "路径备份"]


@pytest.fixture
async def seeded_repo(async_session) -> AssetRepository:
    repo = AssetRepository(async_session)
    for name in NAMES:
        await repo.create(type="character", name=name)
    await async_session.flush()
    return repo


async def _names(repo: AssetRepository, q: str) -> set[str]:
    return {a.name for a in await repo.list(type=None, q=q, limit=100, offset=0)}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("q", "expected"),
    [
        ("alice", {"Alice", "ALICE 战斗装"}),
        ("ALIC", {"Alice", "ALICE 战斗装", "alicia"}),
        ("%", {"100% 纯度"}),
        ("0%", {"100% 纯度"}),
        ("_", {"a_b"}),
        ("a_b", {"a_b"}),
        ("/", {"路径/备份"}),
    ],
)
async def test_list_matches_name_case_insensitively_and_literally(
    seeded_repo: AssetRepository, q: str, expected: set[str]
):
    assert await _names(seeded_repo, q) == expected


@pytest.mark.asyncio
async def test_count_by_type_uses_the_same_match_as_list(seeded_repo: AssetRepository):
    assert await seeded_repo.count_by_type(q="ALICE") == {"character": 2}
    assert await seeded_repo.count_by_type(q="_") == {"character": 1}
