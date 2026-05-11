# MoneyRush

[English README](./README.md)

MoneyRush 是一个面向低延迟盯盘、实时数据沉淀与后续回放/回测扩展的绿色启动项目基座。当前目标聚焦在 Phase 1 的最短闭环：激活股票代码、采集市场数据、落盘时序记录、缓存热点快照，并将实时状态推送到前端看板。

## 当前状态

当前仓库已经包含 Milestone 1 基础骨架，以及第一条 Milestone 2 纵向打通链路：

- FastAPI API 服务，提供健康检查与 symbol 激活接口
- collector 进程，已接通 Redis Streams、模拟市场数据生成，以及 Timescale/PostgreSQL 持久化
- React + Vite 前端壳，支持 symbol 激活、快照面板、WebSocket 实时 market-state 展示
- Docker Compose 编排，包含 TimescaleDB、Redis、API、collector、frontend
- 初始 PostgreSQL/TimescaleDB 表结构引导脚本

## 目录结构

```text
backend/        FastAPI 服务与应用模块
collector/      后台采集进程与市场数据处理逻辑
frontend/       React + Vite 前端看板
infra/          Compose 文件、Dockerfiles、数据库初始化脚本
```

## 快速开始

1. 如需本地覆盖配置，可先复制环境模板：

   ```bash
   cp .env.example .env
   ```

2. 启动整套服务：

   ```bash
   docker compose -f infra/compose/docker-compose.yml up --build
   ```

3. 打开前端：`http://localhost:5173`
4. 在页面中输入股票代码，例如 `000001`，触发激活请求
5. 观察 collector 生成模拟快照、写入市场数据，并通过 WebSocket 向前端持续推送 market-state

默认只对宿主机暴露面向用户的服务：

- frontend：`5173`
- API：`8000`

PostgreSQL/TimescaleDB 和 Redis 默认仅运行在 Docker 内部网络中，不对外暴露端口。这样可以减少本地端口冲突，也能避免不必要的依赖组件暴露。

## 常用接口

- API 根路径：`http://localhost:8000/`
- 存活检查：`http://localhost:8000/api/v1/health/live`
- 就绪检查：`http://localhost:8000/api/v1/health/ready`
- 当前激活 symbols：`http://localhost:8000/api/v1/symbols/active`
- 当前激活 snapshots：`http://localhost:8000/api/v1/symbols/snapshots`
- WebSocket 实时流：`ws://localhost:8000/ws/market`

## 许可协议

本仓库的社区版使用 **GNU AGPL v3**。

- 这是一份标准的强 copyleft 开源许可证
- 如果你修改并以网络服务形式向用户提供该软件，AGPLv3 可能要求你向这些用户提供该修改版本的对应源码
- 如果你不希望承担 AGPLv3 义务，应走单独商业授权路径

完整条款请查看英文正式协议 [`LICENSE`](./LICENSE)。

商业授权说明请查看 [`COMMERCIAL-LICENSE.md`](./COMMERCIAL-LICENSE.md)。

如果你希望先看中文说明，可阅读 [`LICENSE.zh-CN.md`](./LICENSE.zh-CN.md)。该文件是便于理解的中文解读，正式法律文本仍以英文版 `LICENSE` 为准。
