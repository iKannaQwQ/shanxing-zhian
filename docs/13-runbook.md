# 13 · 运行手册（傻瓜版）

> 照着从上往下做即可。每一步都写了「你应该看到什么」，对不上就跳到第 6 节。
> 所有命令都在**项目根目录**执行：`cd ~/Documents/dev/mountain`

---

## 0. 系统由什么组成

| 组件 | 在哪 | 作用 |
|---|---|---|
| 后端 (FastAPI) | 电脑 | 收数据、存数据、推状态、提供网页 |
| AI worker | 电脑 | 跑模型，把感知结果回传给后端 |
| 网页 | 浏览器 | 看状态（用户端 + 管理后台） |
| App | iPhone | 采集 RGB + 深度并上传 |

**四个部分缺一不可。** 手机不在线，网页就一直显示「数据已过期」。

---

## 1. 第一次准备（只做一次）

### 1.1 检查环境

```bash
perception/.venv/bin/python scripts/check_env.py
```

**应看到**：Python / Node / CMake / 风险引擎 逐项 OK。
缺哪项就运行 `bash scripts/bootstrap_mac.sh`。

### 1.2 编译风险引擎（如果没编译过）

```bash
bash scripts/build_risk_engine.sh
```

**应看到**：`rock_risk_cli` 生成，测试 `46/46 用例通过`。

> 这一步 `start_all.sh` 会自动检查，没编译会自动补。

### 1.3 构建网页

```bash
cd frontend
export PATH="/Users/akiyamakanna/Documents/dev/mountain/.tools/node/bin:$PATH"
npm install       # 只第一次
npm run build
cd ..
```

**应看到**：`✓ built in ...`，生成 `frontend/dist/`。

### 1.4 装手机 App

用 Xcode 打开 `ios/RockRoadCapture/RockRoadCapture.xcodeproj`，
选中你的 iPhone，按 **⌘R**。

首次安装后，手机上要在**设置 → 通用 → VPN与设备管理**里信任开发者证书。

---

## 2. 启动（每次开机后）

### 2.1 一条命令

```bash
bash scripts/start_all.sh
```

**应看到**：

```
== 1/4 检查环境 ==
  Python: Python 3.11.x
  风险引擎: 就绪
== 2/4 启动后端 ==
  已启动 (pid xxxxx)，等待就绪…
  后端就绪: http://127.0.0.1:8000
== 3/4 启动 AI worker ==
  模型加载约需 10–20 秒，请稍候
  已启动 (pid xxxxx)
== 4/4 完成 ==

  用户端    http://172.20.10.4:8000/
  管理后台  http://172.20.10.4:8000/#/admin
```

> 记下这个 IP 地址，手机要填它。IP 每次连热点可能变。

### 2.2 确认状态

```bash
bash scripts/status.sh
```

**应看到**（手机还没连时）：

```
后端      : 正常
AI worker : IDLE | 最近结果 None ms
待处理帧  : 0 | 失败/过期: 0
在线设备  : 0 / 0
最新判定  : None | 年龄 None ms | 新鲜
累计判定  : 0 帧
```

### 2.3 打开网页

浏览器访问 **http://127.0.0.1:8000/**（本机）或上一步显示的局域网地址。

---

## 3. 手机端操作

### 3.1 配置服务器地址

1. 打开 App
2. 在「服务器」里填：`172.20.10.4:8000`（换成第 2.1 步显示的实际 IP）
3. 点 **测试连接**

**应看到**：`服务器可达 · inference=external`

> 手机和电脑必须连**同一个 Wi-Fi**（或手机开热点、电脑连上）。

### 3.2 开始采集

1. 点 **开始**
2. 把手机架在沙盘上方（0.64 m，镜头朝下）

**应看到**：

| 位置 | 内容 |
|---|---|
| 状态流 | `已连接（已收 N 条）`，N 持续增长 |
| 端到端风险 | `UNKNOWN / DEGRADED`（没有落石检测器时这是正常的） |
| 已发送 | 持续增长 |
| 队列 | `0 / 32` |

### 3.3 停止采集

1. 点 App 里的 **停止**

> **必须先停手机，再停电脑服务。** 否则手机会一直重试上传。

---

## 4. 看数据

| 页面 | 地址 | 看什么 |
|---|---|---|
| 用户端 | `/#/` | 道路状态、双向通行、落石、最新画面 |
| 运行总览 | `/#/admin` | AI FPS、在线设备、系统状态、最新预警 |
| 实时监控 | `/#/admin/monitor` | RGB + 分割叠加 + 检测框 + 深度图 |
| 事件管理 | `/#/admin/events` | 落石事件、截图、处理状态 |
| 历史数据 | `/#/admin/history` | 风险时间轴、历史图像 |
| 设备管理 | `/#/admin/devices` | 在线状态、FPS、延迟、丢帧 |

页面底部有一行小字，排障时先看它：

```
web m6.1 · 视口 1440px · API http://172.20.10.4:8000 · WS ws://172.20.10.4:8000/ws/status
```

---

## 5. 关闭（严格按顺序）

### 5.1 先停手机

App 里点 **停止**，然后可以退出 App。

### 5.2 再停电脑

```bash
bash scripts/stop_all.sh
```

**应看到**：

```
== 停止服务 ==
  停止 AI worker (pid xxxxx)…
  AI worker 已停止
  停止 后端 (pid xxxxx)…
  后端 已停止

端口 8000 已释放
```

### 5.3 确认真的停了

```bash
bash scripts/status.sh
```

**应看到**：`后端未运行`。

如果显示端口仍被占用，按提示 `kill -9 <PID>`。

---

## 6. 出问题怎么办

| 现象 | 最可能原因 | 处理 |
|---|---|---|
| `缺少 Python 环境` | venv 没建 | `bash scripts/bootstrap_mac.sh` |
| `风险引擎编译失败` | 缺 CMake / 编译器 | 看 `data/logs/build_engine.log` |
| 网页打不开 | 后端没起 | `bash scripts/status.sh` 看状态 |
| 网页显示「后端不可达」 | 端口不对 / 后端崩溃 | 看 `data/logs/backend.log` |
| 手机「服务器不可达」 | IP 变了 / 不在同一网络 | 重新看 `start_all.sh` 输出的 IP |
| 状态流一直「连接中…」 | WebSocket 被防火墙拦 | 检查 Mac 防火墙，或换热点 |
| 一直「数据已过期」 | 手机没在采集 / AI worker 挂了 | 看 `/admin` 里 AI Worker 状态 |
| AI worker 显示「停滞」 | worker 进程挂了 | `tail -50 data/logs/ai_worker.log` |
| 事件页截图显示「已清理」 | 超出保留窗口（24h） | 正常现象，数据已按策略删除 |
| 深度图纯黑 | 手机没拿到 LiDAR 深度 | 确认是带 LiDAR 的机型，且距离 >0.2 m |

### 看日志

```bash
tail -50 data/logs/backend.log     # 后端
tail -50 data/logs/ai_worker.log   # AI worker
```

### 前后端一键自检

```bash
perception/.venv/bin/python scripts/smoke_test_backend.py
```

**应看到**：`全部通过 (10/10)`。

---

## 7. 数据在哪、怎么清理

| 内容 | 位置 | 说明 |
|---|---|---|
| 数据库 | `data/db/mountain.db` | 帧、判定、事件、设备 |
| 原始帧 | `data/artifacts/<frame_id>/` | 每帧 4 个文件 |
| 日志 | `data/logs/` | 后端 / worker 日志 |
| 进程号 | `data/run/` | 启停脚本用 |

### 保留策略

默认保留 **24 小时**，后台每 2 秒检查一次并自动清理。

改保留时长：`ROAD_RETENTION_HOURS=72 bash scripts/start_all.sh`

### 彻底重置（清空所有历史）

```bash
bash scripts/stop_all.sh
rm -rf data/db/mountain.db* data/artifacts/*
bash scripts/start_all.sh
```

> 建议在正式沙盘实验前重置一次，避免测试数据混进历史曲线。

---

## 8. 不用沙盘也能先验证一遍

想确认整条链路通不通，用模拟落石：

```bash
# 先启动服务
bash scripts/start_all.sh

# 让 worker 处理一帧并注入合成落石（标注为 SIMULATED）
perception/.venv/bin/python scripts/ai_worker.py --once --simulate-rock high
```

然后刷新用户端：应看到 **红色 HIGH / 阻断**，并且顶部有 **DEMO 数据** 横幅。

> 这是唯一让页面出现非 UNKNOWN 状态的方法，用于验证链路，不代表真实检测能力。

---

## 9. Windows（**尚未实机验证**）

步骤见 [docs/10-backend-api.md](10-backend-api.md) 第 2 节。
`scripts/build_risk_engine.ps1` 与上述 `.sh` 脚本**都未在 Windows 上执行过**，
可作为初稿使用，遇到问题需要现场调整。
