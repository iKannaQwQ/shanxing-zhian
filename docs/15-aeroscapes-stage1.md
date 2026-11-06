# 15 · AeroScapes 训练 · Stage 1 审计与方案

> 本文件只记录**核实过的事实**与**待决策项**。Stage 1 不下载数据、不训练、不改接口。
> 核实时间：2026-11-06。

---

## 1. 结论摘要

| 问题 | 答案 |
|---|---|
| 官方入口是否有效 | ✅ 有效，README 与 LICENSE 均可取 |
| 下载链接是否可用 | ✅ 可用（Google Drive，需 confirm token） |
| 许可证是否核实 | ✅ 仓库根目录 `LICENSE` 全文为 **CC BY-SA 4.0** |
| 现有代码能否直接吃 AeroScapes | ❌ **不能直接用**，U-Net 与推理接口可复用，训练/数据层需改造 |
| 类别 ID 能否预先确定 | ❌ **不能**，官方 README 与仓库均无类别定义，必须解析实际 mask |
| 能否按视频序列划分 | ⚠️ **未知**，README 未提供序列归属，需下载后检查 `ImageSets` |

**一句话：可以开工，但必须先补数据层工具，且标签映射只能在拿到数据后才能定。**

---

## 2. 官方来源核实

### 2.1 README 原文要点

来源：`https://raw.githubusercontent.com/ishann/aeroscapes/master/README.md`（1,575 字节）

| 项 | 原文 |
|---|---|
| 规模 | `3269 720p images and ground-truth masks for 11 classes` |
| 拍摄高度 | `altitude range of 5 to 50 metres` |
| 目录 | `JPEGImages/`、`SegmentationClass/`、`Visualizations/`、`ImageSets/` |
| 下载 | Google Drive（见 §2.3） |
| 引用 | Nigam, Huang, Ramanan. *Ensemble Knowledge Transfer for Semantic Segmentation*. WACV 2018 |
| 致谢 | Autel Robotics 负责采集与人工标注 |

**注意**：README **没有**任何许可证声明，也没有类别列表。任务书中"GitHub 仓库标注 CC BY-SA 4.0"的说法，实际依据是仓库的 `LICENSE` 文件（§2.2），不是 README。

### 2.2 许可证（已读全文）

| 项 | 值 |
|---|---|
| 文件 | `https://raw.githubusercontent.com/ishann/aeroscapes/master/LICENSE` |
| 大小 | 20,131 字节（CC 官方法律文本全文） |
| 标题行 | `Attribution-ShareAlike 4.0 International` |
| 结论 | **CC BY-SA 4.0**，非 CC BY-NC，**允许商业使用** |

义务：

1. **署名**：标注作者、数据集名、许可证链接；
2. **保留声明**：衍生作品须保留原许可证说明；
3. **相同方式共享**：若公开发布衍生作品，须以同等许可发布。

**风险（必须记录，不能回避）**：训练得到的**模型权重**是否属于 CC 定义下的 "Adapted Material"，法律上无定论。保守做法有二：

- 权重也按 CC BY-SA 4.0 发布；
- 或仅在内部使用、不对外分发权重。

本项目当前为研究用途，建议**出处标注 + 权重暂不对外分发**，并在 README 与模型元数据里写清来源。

### 2.3 下载入口（已实测）

| 项 | 值 |
|---|---|
| 分享页 | `https://drive.google.com/file/d/1WmXcm0IamIA0QPpyxRfWKnicxZByA60v/view` |
| 分享页状态 | HTTP 200 |
| 实际文件 | `aeroscapes.tar.gz` |
| 文件大小 | **788,340,338 字节 = 751.8 MiB** |
| 直接 GET | 303 → 病毒扫描确认页，需 `confirm=t` + `uuid` |
| 带 token 的 Range 请求 | HTTP 206，`content-range: bytes 0-1023/788340338` ✅ |

> 结论：链接有效且文件可下载；但**需要处理 Google Drive 的 confirm token 流程**
> （`gdown` 或自行解析表单）。Stage 2 我会写成脚本，不手工点。

### 2.4 仓库内容

仓库共 3 个条目：`LICENSE`、`README.md`、`assets/`。

**没有代码、没有类别定义、没有 ImageSets 内容。** 这意味着：

- 类别 ID 必须在下载后从 `SegmentationClass/*.png` 的像素值反推；
- 划分文件长什么样、是否含序列信息，也必须下载后才能确认。

论文 PDF 可达：`https://www.cs.cmu.edu/~deva/papers/aeroscapes.pdf`（HTTP 206）。

---

## 3. 代码审计

### 3.1 可直接复用

| 文件 | 现状 | 复用理由 |
|---|---|---|
| `perception/src/perception/road/unet.py` | `UNet(in_channels=3, base=32)`，输出 1 通道，**1,927,009** 参数；损失 = BCE + Dice | 二分类道路分割正是本次任务形态，无需改结构 |
| `perception/src/perception/road/unet_segmenter.py` | `predict(rgb_bgr, rotation_cw) → (mask, confidence)` | 推理接口稳定，后端已在用 |
| `perception/src/perception/service.py` | `road_model` 参数决定 YOLOP / U-Net | `ROAD_ROAD_MODEL` 切换机制现成 |
| `backend/app/config.py` | `road_model` 环境变量 | 无需改动 |
| 契约 `perception_report` | `road.coverage_ratio / confidence / components / status` | JSON 结构不动 |

### 3.2 必须改造（改动集中在训练侧，不碰线上链路）

| 文件 | 缺口 | 计划 |
|---|---|---|
| `perception/tools/train_road_unet.py`（157 行） | ① 默认输入 `480x320`，任务要求 512×512<br>② 增强只有水平翻转 + 亮度，**无同步几何增强**<br>③ **无断点续训**<br>④ **无独立 test 集评估**<br>⑤ 指标只有 IoU/Dice，**无 Precision/Recall/PixelAcc/混淆矩阵**<br>⑥ 报告缺随机种子、数据集版本、耗时分解 | 扩写：`--size 512x512`、同步旋转/裁剪/缩放（mask 用最近邻）、`--resume`、`--seed`、完整指标与 `config.json` |
| 新增 `perception/tools/prepare_aeroscapes.py` | 不存在 | 解压校验 → 类别直方图 → 二分类 mask → 划分 → 可视化 |
| 新增 `perception/tools/evaluate_unet.py` | 不存在 | test 集指标 + 混淆矩阵 + RGB/GT/Pred 三联图 |
| `perception/src/perception/road/unet_segmenter.py` | 输出缺"模型版本 + 输入尺寸" | 读权重里的元数据并随结果返回 |

### 3.3 不动的东西（明确保护）

- `backend/` 全部、`frontend/` 全部、`risk_engine/` 全部
- `contracts/schemas/*`（JSON 结构）
- 现有 `perception/tests/` 的 71 项测试
- 现有权重文件（当前 `perception/weights/` 为空，不存在覆盖风险）

---

## 4. 标签映射（待数据确认，不预设）

README 只给"11 个类"，**没有类别表**。因此本阶段**拒绝预先写死类别 ID**。

Stage 2 的解析顺序：

1. 读取 `SegmentationClass/*.png` 全部像素值 → 得到唯一值集合；
2. 统计每个值的像素占比与出现该值的图像数；
3. 用 `Visualizations/`（官方 RGB 可视化）反查每个 ID 对应的语义颜色；
4. 对照论文中的类别描述确认名称；
5. 生成 `class_map.yaml` 后再做二分类。

映射结构（值待填）：

```yaml
# 仅结构示意，ID 必须来自实际解析结果
classes:
  <id>: {name: <name>, role: road | nonroad | ignore}
binary:
  road:    [<id>, ...]
  nonroad: [<id>, ...]
  ignore:  [<id>, ...]
```

硬性规则（任务要求，已写入脚本约束）：

- Road → 前景 1；其余有效类 → 0；
- **未知/无效像素一律 0，绝不默认成道路**；
- `Obstacle` ≠ `Rockfall`，不参与落石检测；
- `Vegetation` 只是"非道路"，不额外解释成障碍；
- 原始多分类 mask **保留不覆盖**，二分类另存目录。

---

## 5. 数据划分与泄漏风险

| 项 | 现状 |
|---|---|
| 官方划分 | `ImageSets/` 存在，但**内容未知**（仓库未公开） |
| 视频序列数 | 论文称 141 段，README 未提 |
| 图像与序列的对应关系 | **未知**，文件名是否含序列号需下载后确认 |

计划：

1. 先解析 `ImageSets/` 的实际文件（可能是 `train.txt` / `val.txt`）；
2. 若文件名含序列前缀（如 `seq03_frame0123.png`），按序列分组划分；
3. 若无法获得序列信息，则：
   - train/val 用官方划分（若有）；
   - **test 从 train 中按固定种子切出**，并**在报告中明确写明"非官方测试集"**；
   - 保留 `split.json` 记录种子与比例，保证可复现。

> 不做的事：不把重新划分的结果称为"官方测试集"；不用 test 调参。

---

## 6. 训练方案调整

在现有脚本基础上改，不重写：

| 项 | 方案 |
|---|---|
| 输入 | 统一 **512×512**（实验 A/B）；实验 C 对比 256/384/512 |
| mask 插值 | **最近邻**，禁止双线性（会污染类别 ID） |
| 增强（实验 B） | 水平翻转 + 旋转 ±10° + 随机裁剪缩放 + 亮度对比度 + 轻微色彩抖动，**几何变换与 mask 同步** |
| 损失 | 沿用 BCE + Dice |
| 优化器 | Adam + CosineAnnealing（沿用） |
| 设备 | `auto`：MPS → CPU →（未来）CUDA；**不把 CUDA 写成必需** |
| 断点续训 | 每 epoch 存 `last.pt`（含 optimizer/scheduler/epoch/rng），`--resume` 恢复 |
| 复现 | 记录 seed、torch 版本、数据集版本（tar 包大小 + 文件数）、增强参数 |
| 最佳权重 | 按 val Road IoU 保存，**不覆盖旧权重**（新路径 `perception/weights/aeroscapes_unet_*.pt`） |

---

## 7. 资源估算

| 项 | 估算 | 依据 |
|---|---|---|
| 下载 | 751.8 MiB | 实测 content-length |
| 解压后 | 约 2–3 GB（估算） | 3269 张 720p JPEG + 等量 PNG mask + RGB 可视化 |
| 磁盘余量 | 189 GB 可用 | 实测 |
| 训练内存 | batch 4 @512² 预计 <4 GB | U-Net 仅 1.9 M 参数 |
| 训练时长 | **待实测**，先跑 1 epoch 计时 | MPS 上粗估 4–8 分钟/epoch；40 epoch 约 3–5 小时 |
| CPU 训练 | 可行但慢，**不作为主路径** | 仅用于兼容性验证 |

> 不做没有信息量的长跑：第一次先跑 1–2 epoch 拿真实速度，再决定总轮数。

---

## 8. 合规与交付口径

- 训练产出的模型**不得**宣称"能准确识别山区公路"。数据集是无人机俯视 5–50 m，
  与本项目固定机位（约 0.64 m 俯视）仍有差异，报告里必须写明。
- 模型卡（model card）需包含：数据集来源与许可证、训练超参、test 集指标、
  **已知失效场景**。
- 不重新分发原始数据。
- 论文引用与作者署名写入 README。

---

## 9. Stage 2 执行计划（待你确认后开始）

1. 写 `prepare_aeroscapes.py` 的下载器（处理 Drive confirm token）；
2. 解压 → 结构校验（数量、尺寸、损坏、空标注、编码）；
3. **先出类别解析报告**（唯一值 + 占比 + 可视化对照），再定映射；
4. 生成二分类 mask + 划分 + 30 组可视化；
5. 提交 `class_map.yaml` 与数据集报告，**等你确认映射后再训练**。

---

## 10. 需要你确认的三件事

| # | 决策 | 我的建议 |
|---|---|---|
| 1 | 是否现在下载 751.8 MiB 数据 | 建议下载，磁盘够 |
| 2 | 模型权重的分发口径（是否按 CC BY-SA 4.0 公开） | 建议先内部使用 + 出处标注，不对外分发 |
| 3 | 若 `ImageSets` 无法按序列分组，是否接受"随机 test 集"并在报告中标注 | 建议接受，但报告必须写明限制 |
