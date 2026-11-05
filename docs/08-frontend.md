# 08 · 前端（Vue 3 + TypeScript + Element Plus）

> **状态：按 M1–M9 分阶段重建中。**
> 已完成：M1（工程结构）、M2（用户端首页）、M3（运行总览）、M4（实时监控）。
> M5 事件与历史 / M6 设备管理尚未开始，侧边栏中标为「待开发」。
>
> 目录结构：
> ```
> src/
> ├── api/          types.ts（枚举与实体）、client.ts、mappers.ts
> ├── composables/  useStatusStream（WS）、useSystemStatus（轮询兜底）
> ├── layouts/      UserLayout、AdminLayout
> ├── views/user/   HomeView（M2）
> └── views/admin/  OverviewView（M3）、MonitorView（M4）
> ```
>
> **枚举词表**：产品侧通行状态为 PASSABLE / PARTIALLY_BLOCKED / BLOCKED / UNKNOWN，
> 后端契约当前使用 RESTRICTED 表示部分受阻，映射集中在 `api/mappers.ts` 一处。
> 后端是否改名待定（会影响 C++ 引擎与契约）。

> 两个页面：实时状态（用户）、管理后台。
> 构建产物由后端直接托管，一个地址访问，WebSocket 同源可用。

---

## 1. 技术选型

| 项 | 选择 | 理由 |
|---|---|---|
| 框架 | Vue 3 + `<script setup>` | 组合式 API，逻辑与模板就近 |
| 构建 | Vite 5 | 启动快、构建配置极少 |
| 组件库 | Element Plus | 表格/分页/标签开箱即用 |
| 路由 | vue-router（**hash 模式**） | 静态托管下刷新不会 404，省掉服务端 SPA 回退配置 |
| 状态 | 组件内 `ref` + 一个 WS composable | 页面少，不引入 Pinia |

---

## 2. 目录

```
frontend/
├── index.html
├── vite.config.ts      # 开发期把 /api 与 /ws 代理到 127.0.0.1:8000
├── tsconfig.json
└── src/
    ├── main.ts         # 挂载 Element Plus + router
    ├── App.vue         # 顶栏 + 路由出口 + 页脚声明
    ├── router.ts       # / 与 /admin
    ├── styles.css      # 风险配色与栅格
    ├── api/
    │   ├── client.ts   # 类型定义 + fetch 封装 + wsURL()
    │   └── mappers.ts  # 数据库行 → 推送消息结构
    ├── composables/
    │   └── useStatusStream.ts   # WebSocket 订阅 + 断线重连
    └── views/
        ├── LiveView.vue   # 实时状态
        └── AdminView.vue  # 管理后台
```

---

## 3. 数据流

```
初始加载  GET /api/v1/status          → 最近判定（兜底）
          GET /api/v1/events?limit=6  → 事件列表
          GET /api/v1/assets/...      → 静态资源

实时      WS  /ws/status              → 每帧 assessment 推送
          断线后 2 秒重连；未连接时由 10 秒轮询兜底
```

实时页优先显示 WebSocket 推来的数据；WS 未连上时回退到轮询得到的最近判定。
两者共用同一个 `AssessmentMessage` 结构（`mappers.rowToMessage` 负责转换）。

---

## 4. 实时页展示什么

| 区块 | 内容 |
|---|---|
| 顶部横幅 | 总体风险（四色）+ 系统状态 + 处理耗时 + 该等级的解释文案 |
| 方向卡片 | A→B / B→A 的通行性（可通行/受限/阻断/未知） |
| 替代通行 | 可确认可用 / 确认不可用 / 无法确认 / 无需替代 |
| 观测质量 | 道路覆盖率、落石数、道路掩码状态、是否降级 |
| 最近事件 | 风险变化、队列溢出、客户端丢帧等 |
| 免责声明 | 固定文案：实验性辅助评估，不作为驾驶指令 |

**风险等级文案刻意不写"安全"**：`SAFE` 显示为「未见侵入」而不是「安全」，
因为它的真实含义是"在本次观测范围内没发现侵入"，不是道路绝对安全。

---

## 5. 管理后台

四个 Tab：

| Tab | 数据来源 |
|---|---|
| 概览 | `/api/v1/status`（5 秒自动刷新） |
| 历史判定 | `/api/v1/assessments`（分页 20/页） |
| 事件流水 | `/api/v1/events`（最近 50 条） |
| 设备 | `/api/v1/devices` |

---

## 6. 构建与运行

```bash
cd frontend
export PATH="$PWD/../.tools/node/bin:$PATH"
npm install
npm run build          # 产出 frontend/dist
```

后端启动时会自动挂载 `frontend/dist`：

```bash
bash scripts/start_backend.sh
# 浏览器打开 http://<电脑IP>:8000/
```

**开发模式**（热更新）：

```bash
npm run dev            # http://localhost:5173，/api 与 /ws 自动代理到 8000
```

---

## 7. 验证情况

| 项 | 结果 |
|---|---|
| `npm install` | 69 个包，30 秒 |
| `npm run build` | ✅ 通过，1.07 MB JS（350 KB gzip）+ 365 KB CSS |
| `vue-tsc --noEmit` | ✅ 零类型错误 |
| 静态资源 HTTP | ✅ 200，JS/CSS 均可下载 |
| 后端挂载与 API 共存 | ✅ `/` 返回页面，`/api/v1/health` 正常 |
| **浏览器实际渲染** | ❌ **未验证**（本机没有可用浏览器环境） |

> 构建与类型检查通过 ≠ 页面显示正确。布局、图表、交互需要人工看一眼。

---

## 8. 已知问题

1. **未做视觉验证**：我只验证了构建、类型、资源可访问，没在浏览器里看过。
2. **包体积偏大**：Element Plus 全量引入，1 MB JS。要优化可改按需引入。
3. **无鉴权**：管理后台和实时页对局域网内任何人可见。
4. **无历史图表**：只有表格，没有趋势曲线。
5. **UNKNOWN 是当前常态**：落石检测器未部署，绝大多数帧会判定为
   `UNKNOWN / NO_ROAD` 或 `UNKNOWN / DEGRADED`。这是系统在正确地说"我不知道"，
   不是前端 bug。
