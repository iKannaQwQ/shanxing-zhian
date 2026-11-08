"""YOLOP 可行驶区域分割适配（M2 的模型侧）。

只使用 drivable-area 头；lane-line 头被显式忽略——
本项目道路没有标线，用车道线推道路边界是我们明确拒绝的做法。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

DEFAULT_REPO = Path(__file__).resolve().parents[3] / "third_party" / "YOLOP"


class YolopRoadSegmenter:
    """加载 YOLOP 权重，输出与原图同分辨率的可行驶区掩码。"""

    def __init__(self, repo_dir: str | Path | None = None,
                 weights: str | Path | None = None,
                 device: str = "auto", img_size: int = 640):
        repo = Path(repo_dir) if repo_dir else DEFAULT_REPO
        if not (repo / "lib" / "models" / "YOLOP.py").exists():
            raise FileNotFoundError(f"未找到 YOLOP 仓库: {repo}")
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))

        import torch
        from lib.config import cfg
        from lib.models import get_net
        from lib.utils import letterbox_for_img

        self._torch = torch
        self._letterbox = letterbox_for_img
        self.img_size = img_size
        self.rotation_cw = 0
        self.device = self._resolve(device)
        self.model = get_net(cfg)
        w = Path(weights) if weights else repo / "weights" / "End-to-end.pth"
        self.model.load_state_dict(self._load(w))
        self.model = self.model.to(self.device).eval()
        # 必须在 self.model 赋值之后统计参数量
        self.model_info = {"road_segmenter": "yolop",
                           "input_size": [int(img_size), int(img_size)],
                           "params": int(sum(p.numel() for p in self.model.parameters())),
                           "device": str(self.device)}

    def _resolve(self, name: str):
        import torch
        if name != "auto":
            return torch.device(name)
        if torch.cuda.is_available():
            return torch.device("cuda:0")
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")

    def _load(self, path: Path) -> dict:
        import torch
        try:
            ckpt = torch.load(path, map_location=self.device, weights_only=True)
        except Exception:
            ckpt = torch.load(path, map_location=self.device, weights_only=False)
        sd = ckpt["state_dict"] if isinstance(ckpt, dict) and "state_dict" in ckpt else ckpt
        return {k.replace("module.", "", 1): v for k, v in sd.items()}

    def predict(self, rgb_bgr: np.ndarray, rotation_cw: int = 0
                ) -> tuple[np.ndarray, float]:
        """返回 (掩码 HxW uint8 {0,1}, 置信度 0-1)。

        rotation_cw: 为让画面正立需要顺时针旋转的角度（0/90/180/270）。
        ARKit 的原生缓冲恒为 landscape-right，竖持时道路会横躺；
        直接送模型会让分割质量静默下降，因此**先转正再推理，再把掩码转回来**。

        只在模型输入端旋转，不动原始图像与内参——几何链路完全不受影响。
        """
        import cv2
        import torch
        import torch.nn.functional as F
        import torchvision.transforms as transforms

        if rgb_bgr is None or rgb_bgr.ndim != 3:
            raise ValueError("输入应为 HxWx3 BGR 图像")

        h0, w0 = rgb_bgr.shape[:2]
        # 显式传参优先；否则用实例设置（后端在每帧前设置 self.rotation_cw）
        rotation_cw = rotation_cw or getattr(self, "rotation_cw", 0)
        work = rgb_bgr
        if rotation_cw == 90:
            work = cv2.rotate(rgb_bgr, cv2.ROTATE_90_CLOCKWISE)
        elif rotation_cw == 180:
            work = cv2.rotate(rgb_bgr, cv2.ROTATE_180)
        elif rotation_cw == 270:
            work = cv2.rotate(rgb_bgr, cv2.ROTATE_90_COUNTERCLOCKWISE)

        img, ratio, pad = self._letterbox(work, new_shape=self.img_size, auto=True)
        pad_w, pad_h = int(pad[0]), int(pad[1])

        tf = transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])
        x = tf(img).unsqueeze(0).to(self.device).float()

        with torch.no_grad():
            _, da_out, _ = self.model(x)          # lane-line 分支刻意丢弃

        h, w = x.shape[-2:]
        da = da_out[:, :, pad_h:h - pad_h, pad_w:w - pad_w]
        up = max(1, int(round(1.0 / float(ratio[0]))))
        da = F.interpolate(da, scale_factor=up, mode="bilinear")
        prob = torch.softmax(da, dim=1)
        conf = float(prob.max(dim=1).values.mean().item())
        mask = prob.argmax(dim=1)[0].to(torch.uint8).cpu().numpy()

        if mask.shape != work.shape[:2]:
            mask = cv2.resize(mask, (work.shape[1], work.shape[0]),
                              interpolation=cv2.INTER_NEAREST)
        # 转回原始朝向，后续几何计算仍以原始图像为基准
        if rotation_cw == 90:
            mask = cv2.rotate(mask, cv2.ROTATE_90_COUNTERCLOCKWISE)
        elif rotation_cw == 180:
            mask = cv2.rotate(mask, cv2.ROTATE_180)
        elif rotation_cw == 270:
            mask = cv2.rotate(mask, cv2.ROTATE_90_CLOCKWISE)

        if mask.shape != (h0, w0):
            mask = cv2.resize(mask, (w0, h0), interpolation=cv2.INTER_NEAREST)
        return mask.astype(np.uint8), conf
