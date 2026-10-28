# 电商 ERP 系统

面向电商企业的一体化进销存 ERP 工作台。

## 技术栈

- 后端：Python 3.12 + FastAPI + SQLAlchemy 2.0 + Alembic + MySQL 8 + Redis
- 前端：Vue 3 + TypeScript + Vite + Element Plus

## 目录结构

```text
backend/   后端（FastAPI）
frontend/  前端（Vue 3）
docs/      需求、路线图与任务卡
scripts/   文档与工具脚本
```

## 本地启动

开发数据库固定使用本机 MySQL 服务 `MYSQL5`（`127.0.0.1:3306`，数据库 `erp`），连接配置以根目录 `.env` 为准。不要执行 `docker compose up -d`，也不要单独启动 Compose 中的 `mysql` 服务；Docker MySQL 历史端口 `3307` 不再属于本项目开发路径。Docker Redis 仍可按需使用。

```bash
# 1. 如使用 Docker Redis，仅启动 Redis（不要启动 mysql）
docker compose up -d redis

# 2. 后端
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload

# 3. 前端
cd frontend
npm install
npm run dev
```

后端健康检查：`GET http://localhost:8000/health`，接口文档：`http://localhost:8000/docs`。

## 文档入口

- [API 使用说明](docs/API-使用说明.md)与[OpenAPI 快照](docs/openapi.json)
- [用户手册](docs/用户手册.md)
- [运维手册](docs/运维手册.md)
- [遗留项与二期清单](docs/遗留项与二期清单.md)

## Docker 部署

容器化部署使用独立配置，连接容器外 MySQL，不会启动 Docker MySQL。准备部署环境、
运行一键部署脚本和进行冒烟测试的步骤见 [deploy/README.md](deploy/README.md)。
