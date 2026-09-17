"""安装记录摘要是持久化契约：跨进程、跨数据库读回都须得到同一值。"""

import hashlib

from lib.market.installations import definition_digest


def test_digest_ignores_key_order_and_hashes_compact_unescaped_utf8():
    reordered = {"meta": {"name": "示例", "author": "A"}, "kind": "declarative"}
    original = {"kind": "declarative", "meta": {"author": "A", "name": "示例"}}

    expected = hashlib.sha256('{"kind":"declarative","meta":{"author":"A","name":"示例"}}'.encode()).hexdigest()
    assert definition_digest(reordered) == definition_digest(original) == expected
