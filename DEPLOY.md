# 快速部署（macOS / 开发机）

> 适用：Apple Silicon Mac（M1–M5）。Windows 部署脚本存在但**从未实机验证**。
> 首次安装需要联网下载 Python/Node 依赖；之后的启动不需要网络。

---

## 0. 两种模式

| 模式 | 端口 | 需要模型权重 | 用途 |
|---|---|---|---|
| **demo** | 8001 | ❌ | 合成沙盘演示、汇报截图、UI 验收 |
| **full** | 8000 | ✅ | 真实后端 + AI worker + iPhone 采集 |

demo 使用独立的 `data/demo_sandbox/demo.db`，**不会碰真实数据库**，页面有 DEMO 标识。

---

## 1. 依赖

- macOS Apple Silicon
- [uv](https://docs.astral.sh/uv/)（创建 Python 3.11 环境）
- 网络（首次安装）
- Xcode（仅 iOS 采集端；demo/full 的 Web 部分不需要）

工程内的 Node 与 CMake 由 `scripts/bootstrap_mac.sh` 安装在 `.tools/`，
不会污染系统环境。

---

## 2. 首次安装 + demo（一条命令）

```bash
git clone <你的仓库地址> shanxing-zhian
cd shanxing-zhian

bash scripts/quickstart.sh --install --demo
```

首次会：
1. 安装工程内 Node（v24）与 CMake；
2. 创建 `perception/.venv`（Python 3.11），安装 torch/OpenCV/FastAPI 等；
3. 编译 C++ 风险引擎（48 项测试）；
4. 构建 Vue 前端；
5. 生成 10 个场景的合成演示数据；
6. 在 **8001** 启动演示后端。

打开：

- 用户端：`http://127.0.0.1:8001/`
- 管理后台：`http://127.0.0.1:8001/#/admin`

`Ctrl-C` 停止。之后每次只需：

```bash
bash scripts/quickstart.sh --demo
```

> demo 不加载道路模型，也不需要 `perception/weights/`。
> 演示数据的生成方式与限制见 [docs/24](docs/24-demo-data.md)。

---

## 3. full：完整系统

### 3.1 放入道路权重

仓库不包含模型权重（体积/许可证原因）。把训练好的权重放到：

```bash
perception/weights/aeroscapes_unet_512_bs8_expE.pt
```

或其他路径，然后用环境变量指定：

```bash
ROAD_ROAD_MODEL=perception/weights/<你的权重>.pt \
bash scripts/quickstart.sh --full
```

### 3.2 启动

```bash
bash scripts/quickstart.sh --full
```

它会启动 FastAPI 后端（8000）与 AI worker，并打印本机局域网地址。
iPhone 采集端填 `IP:8000`。

### 3.3 沙盘道路带

- 默认 `ROAD_ROAD_SOURCE=plane`：把整片平整地面当可通行区。
  它只适合桌面/固定俯视的临时演示；在沙盘上会把道路两侧也当成路。
- 要使用 U-Net 道路分割：

```bash
ROAD_ROAD_SOURCE=road_model \
ROAD_ROAD_MODEL=perception/weights/<沙盘微调权重>.pt \
bash scripts/quickstart.sh --full
```

> 沙盘尚未微调时，U-Net 可能仍不准；真实数据采集与训练见
> [docs/21](docs/21-target-domain-capture.md)、[docs/09](docs/09-sandbox-pipeline.md)。

---

## 4. 回归测试

```bash
perception/.venv/bin/python -m pytest perception/tests backend/tests -q   # 99
risk_engine/build/rock_risk_tests                                       # 48/48
perception/.venv/bin/python contracts/validate.py --all                 # 12/12
```

---

## 5. 打包发布

```bash
bash scripts/package_release.sh                  # 源码包（不含权重）
bash scripts/package_release.sh --with-weights   # 本机完整包（含 .pt，勿公开分发）
```

输出在 `dist/`，带 `.sha256`。源码包包含 git 跟踪的文件 + 已构建的 `frontend/dist`，
不包含 `.venv`、`.tools`、`data/`、`perception/weights/`。

---

## 6. 常见问题

| 现象 | 处理 |
|---|---|
| `uv: command not found` | 安装 uv 后重试 |
| `缺少 perception/.venv` | 加 `--install` 首次安装 |
| `端口 8001 已被占用` | `--port 8002` |
| `缺少道路权重` | 把 `.pt` 放入 `perception/weights/`，或先用 `--demo` |
| 监控页整片绿色 | 先确认 mask 本身：道路带覆盖率应约 6.3%；若掩码本身整片，是 `road_source=plane` 的预期行为，不是前端问题 |
| 页面还是旧样式 | 前端有改动时 `--rebuild`，浏览器 `Cmd+Shift+R` |
| 停止服务 | full：`bash scripts/stop_all.sh`；demo：Ctrl-C |
| 日志 | `data/logs/backend.log`、`data/logs/ai_worker.log` |

---

## 7. 仓库不含什么

以下内容被 `.gitignore` 排除，按需本地准备：

- `perception/weights/`（模型权重）
- `data/aeroscapes/`（AeroScapes 数据集）
- `data/sandbox/`、`data/demo_sandbox/`（采集与合成演示数据）
- `data/artifacts/`、`data/db/`、`data/logs/`
- `perception/.venv/`、`.tools/`、`frontend/node_modules/`、`frontend/dist/`
- `yolo11n.pt`（Ultralytics 基础权重，训练时自动下载）
