# 09 · 沙盘数据管线（数据到位前先建好）

> 目标：沙盘一建好，采集 → 标注 → 训练 → 评估 → 切换模型 全部是现成命令，
> 不用再写代码。

---

## 1. 现在是什么状态

| 环节 | 状态 |
|---|---|
| 目录约定 + 数据校验 | ✅ 完成，13 项测试 |
| 数据集划分（分层） | ✅ 完成 |
| 道路分割 U-Net 训练 | ✅ 完成，合成数据冒烟通过（IoU 0.96） |
| 落石检测器训练（ultralytics） | ✅ 脚本完成，**未在真实数据上跑过** |
| 推理适配器 + 模型切换 | ✅ 完成，`ROAD_ROAD_MODEL` 一行切换 |
| **真实沙盘数据** | ❌ 还没有 |

**没有真实数据，所以这些脚本只证明了"管线能跑通"，没有证明"模型能用"。**

---

## 2. 数据目录约定

```
data/sandbox/
├── raw/                     # 采集原始，人工标注
│   ├── images/*.jpg         采集图像（建议 1280×960）
│   ├── masks/*.png          道路掩码：单通道，0=非道路，255=可行驶
│   └── labels/*.txt         YOLO 检测标签（没有落石就不建文件）
└── dataset/                 # 由 prepare 脚本生成
    ├── images/{train,val}
    ├── masks/{train,val}
    ├── labels/{train,val}
    ├── data.yaml
    └── split.json
```

**约定要点**：

| 项 | 规则 | 原因 |
|---|---|---|
| 掩码取值 | 只允许 0 / 1 / 255 | 混入灰度值会让"可行驶"的判定含糊 |
| 掩码尺寸 | 必须与图像**同尺寸** | 训练时按最近邻缩放，尺寸不一致会掩盖标注错误 |
| 标签 | YOLO 5 列，坐标归一化到 [0,1] | 与 ultralytics 直接对接 |
| 无落石的帧 | **不建标签文件**（等价于空标签） | 显式空文件也支持，但没必要 |
| 文件对应 | 三个目录用同一个 stem | 靠文件名关联，不做模糊匹配 |

---

## 3. 采集与标注建议

1. **数量**：至少 300 帧；其中含落石的 ≥ 150 帧。少于这个量级，mAP 没有统计意义。
2. **多样性**：
   - 落石位置：路中、路肩、贴边、两块以上同时出现；
   - 大小：2–10 cm 都要有；
   - 光照：开灯/关灯/侧光各来一批；
   - 视角：模拟实际安装高度（建议 0.4–0.8 m）。
3. **负样本很重要**：空路面、只有阴影、只有碎石纹理而没有立体落石的帧，都要收。
   否则模型会把"地面纹理"学成"落石"。
4. **手机横装**（除非启用自动转正，见 §6）。

---

## 4. 四步流程

### ① 体检 + 划分

```bash
perception/.venv/bin/python perception/tools/prepare_sandbox_dataset.py \
    --raw data/sandbox/raw --out data/sandbox/dataset --val-ratio 0.2
```

体检会拦下：列数不对、类别越界、坐标越界、非数值、掩码尺寸不符、掩码非二值、缺掩码。
**不通过就不划分**——这是故意的，脏数据进了训练集只会浪费几小时。

划分是**分层**的：含落石与不含落石分别按比例抽样，保证验证集里一定有落石帧。

### ② 训练道路分割

```bash
perception/.venv/bin/python perception/tools/train_road_unet.py \
    --dataset data/sandbox/dataset --epochs 40 --size 480x320
```

产出 `perception/weights/road_unet.pt` + `road_unet_report.json`（含每轮 IoU/Dice）。

### ③ 训练落石检测器

```bash
perception/.venv/bin/python perception/tools/train_rock_detector.py \
    --dataset data/sandbox/dataset --epochs 80 --imgsz 960
```

产出 `perception/results/train/rock_sandbox/weights/best.pt` + 指标 JSON。

### ④ 切换后端使用的模型

```bash
# 默认 YOLOP
bash scripts/start_backend.sh

# 换成沙盘微调的 U-Net
ROAD_ROAD_MODEL=perception/weights/road_unet.pt bash scripts/start_backend.sh

# 落石检测器一起换
ROAD_ROAD_MODEL=perception/weights/road_unet.pt \
ROAD_ROCK_WEIGHTS=perception/results/train/rock_sandbox/weights/best.pt \
bash scripts/start_backend.sh
```

`/api/v1/health` 会返回当前使用的 `road_model`，便于确认切换生效。

---

## 5. 为什么是 U-Net 而不是微调 YOLOP

| 方案 | 问题 |
|---|---|
| 微调 YOLOP | 它的训练管线要求 BDD100K 四件套（图像 / 检测标签 / 可行驶掩码 / **车道掩码**）。沙盘没有车道线，凑不齐；强行造空车道掩码等于灌噪声 |
| U-Net 二分类 | 只需要"图像 + 道路掩码"，沙盘数据天然具备；参数量 1.9 M，CPU 几十分钟可收敛 |

YOLOP 保留为默认选项（在真实道路图像上仍是最强基线），
沙盘场景用 U-Net，两者接口一致，由 `ROAD_ROAD_MODEL` 切换。

---

## 6. 设备朝向（重要）

ARKit 的 `capturedImage` 恒为 landscape-right。手机竖持时画面里的道路是横躺的。

- 系统已经按 `capture.device_orientation` **自动转正**（见 docs/03-perception.md §4.9）；
- 但**建议仍然横装**：转正会多一次旋转开销，而且竖装的视野比例不一定适合沙盘。

---

## 7. 合成数据冒烟结果（证明管线可跑）

真实数据未到，用 `make_synthetic_dataset.py` 生成 20 帧（弯曲路面 + 落石），
跑通了完整链路：

```
体检:   图像 20  掩码 20  标签 20（含框 16，空标签 4）  检测框 35
划分:   训练 15  验证 5（验证集中含落石 4 帧）
训练:   5 epoch / CPU / 320×240
        loss 0.9457 → 0.5562
        IoU  0.3696 → 0.9624
推理:   合成图覆盖率 0.352，掩码尺寸与输入一致
        竖持模拟 + 旋转纠正 → 与正立推理 100% 像素一致
```

> 这些数字只说明"代码路径正确"，**不代表真实性能**。

---

## 8. 真实数据到位后还要做什么

1. **重跑一遍上面的流程**，记录真实的 IoU / mAP。
2. **深度精度标定**：用已知高度的台阶量 LiDAR 的读数误差（目前只知道对齐关系成立，
   不知道米制精度）。
3. **调整风险阈值**：`risk_engine` 里的 `vehicle_width_m` / `min_pass_width_m`
   全是占位值，必须用沙盘的实测尺寸替换。
4. **闭环验证**：把真实判定结果和人工判断对比，看误报/漏报分布。
