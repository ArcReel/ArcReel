"""PROTOTYPE — 把 schema / 模板 / fixtures / 纯模块内联进 demo_shell.html，产出单文件 demo.html。

用法：uv run python lib/custom_provider/prototype_declarative_endpoint/build_demo.py
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent


def main() -> None:
    schema = json.loads((HERE / "schema.json").read_text(encoding="utf-8"))
    templates = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted((HERE / "templates").glob("*.json"))}
    fixtures = json.loads((HERE / "fixtures.json").read_text(encoding="utf-8"))
    module = (HERE / "declarative_endpoint.js").read_text(encoding="utf-8")
    shell = (HERE / "demo_shell.html").read_text(encoding="utf-8")
    data = json.dumps({"schema": schema, "templates": templates, "fixtures": fixtures}, ensure_ascii=False)
    # </script> 不能出现在内联 JSON / JS 里
    data = data.replace("</", "<\\/")
    module = module.replace("</script>", "<\\/script>")
    out = shell.replace("/*__DATA__*/", data).replace("/*__MODULE__*/", module)
    (HERE / "demo.html").write_text(out, encoding="utf-8")
    print(f"wrote {HERE / 'demo.html'} ({len(out) // 1024} KB)")


if __name__ == "__main__":
    main()
