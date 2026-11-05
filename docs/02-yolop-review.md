# 02 · YOLOP 环境审查与部署报告

> 阶段：原 P4（感知-语义）前置调研　|　状态：**第一阶段完成，原版模型已实际跑通**
> 约束遵守：未修改网络结构、未训练新模型、未下载训练数据集。
> 结论基于 2026-11-05 本机实测；未实测的部分明确标注。

---

## 0. 一句话结论

YOLOP 原版能在 Apple Silicon 上跑通，但**只能通过 CPU 或本项目自写的设备封装使用 MPS**；
它的检测头只有 **1 个"车辆"类，不含落石**，道路分割训练自 BDD100K 城市行车视角，
与本项目"桌面沙盘 + 无标线 + **俯视机位**"的域差异极大（几何见 [docs/12](12-camera-geometry.md)）。**不能声称原版可直接识别落石。**

---

## 1. 获取与完整性

git clone 在本网络下会在 `fetch-pack` 阶段断流（`early EOF`），codeload 亦不可达。
改用 **GitHub API 列文件 + raw.githubusercontent 逐文件拉取**，落在 `perception/third_party/YOLOP/`（已 gitignore）。

| 项 | 值 |
|---|---|
| 仓库 | https://github.com/hustvl/YOLOP （main 分支） |
| 许可证 | MIT |
| 落地文件 | 83 个（跳过 `__pycache__`、示例 GIF、`inference/videos/1.mp4`、3 个 ONNX） |
| 权重 | `weights/End-to-end.pth` |
| 字节数 | 95,756,827 |
| SHA-256 | `620e7495328cfc8e04807c14cbb8f8df9916953359d190cbf494b2d049f03b0c` |
| 完整性核验 | 与 GitHub API 元数据给出的字节数**完全一致** |

> 注：仓库另含 3 个预导出 ONNX（`yolop-320/640/1280.onnx`，按元数据 31.8–35.9 MB），本次未下载。

---

## 2. 代码审查

### 2.1 网络结构

- 单 backbone + 三头多任务：**检测头 + 可行驶区域分割 + 车道线分割**。
- 结构定义在 `lib/models/YOLOP.py`（约 600 行），layer 表形式（YOLOv5 风格），
  三个输出的索引写死在表头：`[24, 33, 42]`（det / da_seg / ll_seg）。
- `MCnet.forward()` 返回 `[det_out, sigmoid(da), sigmoid(ll)]`——**两个分割输出已过 Sigmoid**，
  下游用 `torch.max(..., dim=1)` 取 argmax，等价于 0.5 阈值。这一点容易被误当成 logits 使用。
- 分割头共享信息后回馈检测头（论文卖点），改动任一分支都会影响其他分支。
- 存在 `lib/models/light.py`（轻量版）与 `common2.py`，未被 demo 引用。

### 2.2 权重

- 单一端到端 checkpoint，`checkpoint['state_dict']`，键名无 `module.` 前缀（但代码里做了兼容处理）。
- **torch 2.15 下 `torch.load` 默认 `weights_only=True` 未报错**，说明该 checkpoint 只含张量与基础类型，
  不存在"必须关掉安全加载"的问题（本项目封装仍保留回退分支以防万一）。

### 2.3 训练代码

- 入口 `tools/train.py`，支持 DDP（`torch.distributed.launch`）。
- 配置在 `lib/config/default.py`（yacs），提供**单任务开关**，这对我们非常关键：
  `DRIVABLE_ONLY / LANE_ONLY / DET_ONLY / SEG_ONLY / ENC_SEG_ONLY / ENC_DET_ONLY`。
- 默认 `END_EPOCH=240`、`BATCH_SIZE_PER_GPU=24`、Adam、OneCycleLR、warmup 3 epoch。
- 数据增强：HSV、随机透视、翻转、cutout（YOLOv5 系）。

### 2.4 推理入口

| 入口 | 说明 | 实测 |
|---|---|---|
| `tools/demo.py` | 图片/文件夹/摄像头/视频 | ✅ 可跑（见 §3） |
| `tools/test.py` | 数据集评测 | 未跑（需 BDD100K） |
| `hubconf.py` | `torch.hub` 加载 | 未跑（同样受 CUDA 断言限制） |
| `export_onnx.py` | 导出 ONNX（需 onnx/onnxruntime/onnxsim） | 未跑 |
| `test_onnx.py` | ONNX 推理 | 未跑 |
| `toolkits/deploy/` | C++ TensorRT 部署 | **不可用**，见 §6.3 |

### 2.5 类别配置（关键）

```python
# lib/models/YOLOP.py
self.nc = 1
self.names = [str(i) for i in range(self.nc)]   # → ['0']

# lib/dataset/bdd.py
single_cls = True    # 所有车辆类合并为 0
id_dict_single = {'car': 0, 'bus': 1, 'truck': 2, 'train': 3}
```

**检测头只有 1 类，语义是"车辆"，且 `names` 只是字符串 `'0'`（连类别名都没有）。**
增加落石类不是改个数字：`Detect` 层输出通道 = `na × (nc + 5) = 3 × 6 = 18`，
改成 2 类后需变为 21，**预训练权重形状不匹配，必须重训检测分支**（可只加载 backbone 与分割分支）。

### 2.6 数据集配置

- `BddDataset`：BDD100K，需要 4 个目录（image / det label / da mask / lane mask）。
- `lib/config/default.py` 里写死了作者机器绝对路径：

```python
_C.DATASET.DATAROOT  = '/home/zwt/bdd/bdd100k/images/100k'
_C.DATASET.MASKROOT  = '/home/zwt/bdd/bdd_seg_gt'
_C.DATASET.LANEROOT  = '/home/zwt/bdd/bdd_lane_gt'
```

  这些路径不参与推理，但训练前必须改为配置项（本项目禁止绝对路径）。

### 2.7 依赖与 PyTorch 版本

`requirements.txt` **完全没有版本约束**，且**不含 torch/torchvision**：

```
scipy / tqdm / yacs / Cython / matplotlib>=3.2.2 / numpy>=1.18.5 /
opencv-python>=4.1.2 / Pillow / PyYAML>=5.3 / tensorboardX / seaborn /
prefetch_generator / imageio / scikit-learn
```

README 声明：**Python 3.7、PyTorch 1.7+、torchvision 0.8+、CUDA 10.2**。

推理实际会导入的重依赖（比看上去多）：

- `lib/core/general.py` → matplotlib + scipy + yaml
- `lib/core/postprocess.py` → **scikit-learn**（DBSCAN），而 demo 会 import 它
- `lib/utils/plot.py` → matplotlib
- `lib/utils/utils.py` → prefetch_generator

即：`matplotlib`、`scikit-learn`、`scipy` 都不能省。

---

## 3. 环境配置（macOS / Apple Silicon）

工程内隔离环境 `perception/.venv`（Python 3.11，uv 创建），**未安装任何 CUDA 组件**。

| 包 | 版本 |
|---|---|
| Python | 3.11.18 |
| torch | 2.15.0 |
| torchvision | 0.29.2 |
| numpy | 2.4.6 |
| opencv-python | 5.0.0.93 |
| scipy | 1.17.1 |
| scikit-learn | 1.9.1 |
| matplotlib | 3.11.2 |

锁文件：`perception/requirements.lock.txt`（38 个包）。

**兼容性结论**：代码里没有 `np.int` / `np.float` 等已移除别名（已全仓扫描，0 处），
因此 numpy 2.x、OpenCV 5.0 这套"未来版本"能直接跑通，**不需要为旧依赖降级**。
这是本次审查最重要的兼容性事实之一。

复现：

```bash
source scripts/env.sh
uv venv --python 3.11 perception/.venv
perception/.venv/bin/python -m pip install torch torchvision
perception/.venv/bin/python -m pip install -r perception/third_party/YOLOP/requirements.txt
```

---

## 4. 原版推理验证

### 4.1 三个必须知道的坑（均为实测，不是推测）

**坑 1：`--weights` 参数是坏的。**

`demo.py` 里 `parser.add_argument('--weights', nargs='+')` 使参数变成 **list**，
而 `torch.load(opt.weights)` 只接受路径/文件对象：

```
AttributeError: 'list' object has no attribute 'seek'
```

绕过方式：**不传 `--weights`**，用它的默认值（是字符串 `'weights/End-to-end.pth'`），
并且必须在仓库根目录执行。这是官方代码的真实缺陷。

**坑 2：`select_device` 拒绝 MPS。**

```python
# lib/utils/utils.py:51-57
if device and not cpu_request:
    os.environ['CUDA_VISIBLE_DEVICES'] = device
    assert torch.cuda.is_available(), 'CUDA unavailable, invalid device %s requested' % device
```

实测 `--device mps` 直接断言失败：

```
AssertionError: CUDA unavailable, invalid device mps requested
```

所以**官方 demo 在 Mac 上只有 CPU 一条路**。MPS 需绕开该函数（本项目用 `perception/tools/yolop_infer.py` 封装）。

**坑 3：`show_seg_result(is_demo=True)` 强制 resize 到 1280×720。**

输出图分辨率与输入无关，宽高比被拉伸。BDD100K 原图本就是 1280×720 所以看不出，
但**换成手机竖拍或 4:3 图像时，可视化会变形，且不能直接复用像素坐标**。
后续接入本项目时必须替换这段可视化。

### 4.2 运行结果

官方原版（**未改一行代码**）：

```bash
cd perception/third_party/YOLOP
../../.venv/bin/python tools/demo.py --source inference/images --device cpu --save-dir ../../results/demo_cpu
# Results saved / Done. (0.640s) / inf: 0.0702s per frame
```

6 张图全部成功，可视化输出到 `perception/results/demo_cpu/`。

| 指标 | 结果 |
|---|---|
| 模型加载 | ✅ 权重完整、`strict=True` 无缺失/多余键 |
| 图片推理 | ✅ 6/6 |
| 道路（可行驶区）分割 | ✅ 正常，绿色覆盖路面，边界贴合路缘 |
| 车道线分割 | ✅ 正常，红色线可辨；其中 1 张输出 **0 像素**（画面车道线确实模糊） |
| 目标检测 | ✅ 正常，单类"vehicle"，每张 4–24 个框 |
| 可视化保存 | ✅ 6 张 JPEG 落盘 |

逐图（本项目封装，CPU）：

| 文件 | 耗时 | 可行驶区占比 | 车道线像素 | 检出数 |
|---|---|---|---|---|
| 0ace96c3… | 111 ms | 0.186 | 6584 | 14 |
| 3c0e7240… | 97 ms | 0.095 | 17089 | 24 |
| 7dd9ef45… | 95 ms | 0.128 | 0 | 8 |
| 8e1c1ab0… | 106 ms | 0.269 | 24049 | 4 |
| 9aa94005… | 104 ms | 0.118 | 8598 | 8 |
| adb4871d… | 108 ms | 0.232 | 22253 | 14 |

### 4.3 MPS 是否可信（数值一致性核验）

不只看"没报错"，做了 CPU / MPS 逐项比对：

| 指标 | 结论 |
|---|---|
| 可行驶区占比 | 6/6 完全一致（如 0.1856 vs 0.1856） |
| 车道线像素数 | 6/6 **逐位相同** |
| 检出框数量 | 6/6 一致 |

**结论：MPS 在本模型上数值与 CPU 一致，可放心使用。**

可视化图像之间会出现差异，原因是 demo 里 `numpy.random` 生成随机框颜色。
对照实验：官方 demo 连跑两次，自身差异约 **390,281** 像素；
本项目封装 vs 官方差异 **401,960** 像素——同一量级，说明差异来自颜色随机而非算法。

### 4.4 性能（640×640、fp32、warm）

| 设备 | 中位延迟 | 备注 |
|---|---|---|
| CPU | **118 ms/帧** | 4 线程 |
| MPS | **27.9 ms/帧** | 加速 ≈ **4.2×** |

官方 demo 自报 70 ms/帧，差异原因是它把图信纸化为 640×384（BDD100K 16:9），
像素量约为 640×640 的 60%，与实测换算吻合。

---

## 5. 领域适配分析（核心）

### 5.1 三个不可回避的差距

| 维度 | YOLOP 原版 | 本项目沙盘 | 后果 |
|---|---|---|---|
| 类别 | 1 类：车辆 | 需要"落石" | 落石**永远不会**被检出 |
| 车道线 | BDD100K 有标线 | **完全无标线** | 车道线分支恒输出 0 或噪声，无信息量 |
| 视角/尺度 | 车载前视，1.4 m 高，路面占下半幅 | 桌面 80×60 cm，正俯视 0.64 m | 可行驶区形态、纹理、透视全不同 |

**必须明确：原版模型的"道路分割正常"只在 BDD100K 风格图像上成立，不能外推到沙盘。**

### 5.2 落石类为什么不能"加个头"

- `nc=1 → 2` 会改变 `Detect` 层卷积输出通道（18 → 21），**权重形状不匹配**。
- 只加载 backbone + 分割分支、检测分支随机初始化再训练，是可行路径，
  但等价于**重新训练检测任务**，不是"改动一下"。

### 5.3 建议的适配路线（尚未实施）

1. **砍掉车道线分支**：训练时 `LANE_ONLY=False`，推理时不使用 `ll_seg_out`。
   对本项目它没有信息量，留着只会增加误判面。
2. **保留可行驶区分支作为主干**：先用沙盘实拍微调（哪怕 200–500 张），
   再评估是否需要换更轻的 U-Net。
3. **检测分支重训**：`nc=2`（vehicle, rock）；anchor 必须重聚类
   （`NEED_AUTOANCHOR=True`），因为沙盘里落石只有 2–3 cm，原 anchor 是给真实车辆设计的。
4. **输入分辨率**：0.5 m 距离、约 69° 水平视场时，视野宽约 0.68 m；
   1280 px 宽下约 **0.53 mm/像素**，2 cm 落石≈38 px；降到 640 输入仍≈19 px。
   **像素量不是瓶颈**，瓶颈是域差异。若显存/算力允许，建议 960 起。
5. **兜底不可省**：无论 RGB 侧训到什么程度，深度几何通道都必须独立成立。

### 5.4 必须先做的验证（P4 开工条件）

- [ ] 采集 20–50 张沙盘实拍，**零样本**跑原版，量化可行驶区 mIoU 与漏检率——这是判断"公开数据是否够用"的唯一客观依据。
- [ ] 同一批图跑原版权重，记录车道线分支在有/无标线场景下的输出是否恒 0。
- [ ] 用深度图验证沙盘近距（0.4–0.8 m）LiDAR 精度（对应架构文档 R2 风险）。

---

## 6. Windows x86-64 部署

### 6.1 纯 CPU 路线（无 NVIDIA 卡）

```powershell
# Python 3.11 x64
python -m venv .venv
.venv\Scripts\python -m pip install torch torchvision ^
  --index-url https://download.pytorch.org/whl/cpu
.venv\Scripts\python -m pip install -r requirements.txt
```

- `--index-url .../cpu` 是必须的：Windows 默认 wheel 会拉入 CUDA 运行时（数百 MB 且无卡不可用）。
- 推理侧 `torch.device("cpu")` 即可，Mac 的 MPS 代码路径在 Windows 上会自动落到 CPU。
- 预期速度：与 Mac CPU 同量级或略慢（取决于 CPU），640×384 输入约 **70–150 ms/帧**。

### 6.2 CUDA 路线（有 NVIDIA 卡）

```powershell
.venv\Scripts\python -m pip install torch torchvision ^
  --index-url https://download.pytorch.org/whl/cu124
```

- 代码无需改动，`torch.cuda.is_available()` 为真时选 `cuda:0`。
- 注意 demo 里 `half = device.type != 'cpu'` 会在 CUDA 上启用 FP16；**MPS 也会命中这个条件**，
  所以本项目封装用 `--half` 显式控制，不做自动推断（MPS 的 FP16 支持不完整）。

### 6.3 仓库自带的 C++ 部署：不可用

`toolkits/deploy/CMakeLists.txt` 硬编码了：

```cmake
find_package(ZED 3 REQUIRED)
find_package(CUDA ${ZED_CUDA_VERSION} EXACT REQUIRED)
include_directories(/usr/local/cuda-10.2/include)
include_directories(/usr/include/aarch64-linux-gnu/)   # ARM Linux！
link_directories(/usr/local/zed/lib)                   # ZED 双目相机 SDK
```

结论：**TensorRT + CUDA 10.2 + ZED SDK + aarch64 Linux 路径**，
既不能在无卡 Windows 上跑，也不能直接在现代 TensorRT 上编译。作为参考代码阅读可以，不能作为部署路径。

### 6.4 推荐的 Windows 推理路径

1. **首选 PyTorch CPU**：与 Mac 完全同源，零改动，已验证可行。
2. **备选 ONNX Runtime CPU**：仓库已提供 `yolop-640-640.onnx`，`onnxruntime` 在 Windows 上安装简单、启动快，
   但需自行实现后处理（NMS、分割 argmax、letterbox 还原）。**尚未验证，不要当成已可用。**

### 6.5 未验证项（必须明确）

> 本节所有 Windows 内容是**基于依赖关系的分析，未在 Windows 上实际执行**。
> 按项目原则，P8 之前不得声称"Windows 已验证"。

---

## 7. 已知问题汇总

| # | 问题 | 影响 | 处置 |
|---|---|---|---|
| 1 | `demo.py --weights` 是 list 传参 | CLI 不可用 | 用默认值 / 用本项目封装 |
| 2 | `select_device` 硬断言 CUDA | Mac 无法用 MPS | 已用 `yolop_infer.py` 绕过 |
| 3 | 可视化强制 resize 1280×720 | 变形、坐标不可复用 | 接入时必须替换 |
| 4 | 检测只有 1 类（车辆） | 落石不可检 | 重训检测分支 |
| 5 | 车道线分支对无标线场景无意义 | 增加误判面 | 推理时不使用 |
| 6 | `default.py` 写死作者绝对路径 | 不可移植 | 训练前改配置项 |
| 7 | requirements 无版本约束 | 未来可能漂移 | 已生成 lock，Windows 侧需另锁 |
| 8 | C++ deploy 绑定 TensorRT/ZED/ARM | 不可用 | 不作为部署路径 |
| 9 | 本网络下 git clone 断流 | 影响可复现性 | 已记录 raw 逐文件方案 |

---

## 8. 下一步建议

1. 先做 §5.4 的**零样本实测**（需要你拿手机拍 20–50 张沙盘图），用数据决定"公开数据够不够用"。
2. 并行推进原本计划的 **P1a（C++ 风险引擎）**——它不依赖 YOLOP，可离线用契约样例开发。
3. Windows 环境在 P8 一并验证，现在只交付分析，不声明可用。

---

## 附：文件清单

| 路径 | 说明 |
|---|---|
| `perception/third_party/YOLOP/` | 原版代码 + 权重（gitignore） |
| `perception/tools/yolop_infer.py` | 设备无关推理封装（支持 cpu/mps/cuda，输出 JSON 摘要） |
| `perception/results/demo_cpu/` | 官方原版可视化输出 |
| `perception/results/demo_cpu_runner/`、`demo_mps/` | 封装版 CPU / MPS 输出与 summary.json |
| `perception/requirements.lock.txt` | 实测通过的依赖锁 |
