"""从当前 FastAPI 路由导出 OpenAPI，避免手工维护接口清单。"""

import json
from pathlib import Path

from app.main import app


def main() -> None:
    output = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"
    schema = app.openapi()
    output.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    operations = sum(
        1
        for path in schema["paths"].values()
        for method in path
        if method in {"get", "post", "put", "patch", "delete"}
    )
    print(f"已导出 {len(schema['paths'])} 条路径、{operations} 个操作：{output}")


if __name__ == "__main__":
    main()
