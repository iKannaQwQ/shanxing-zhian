"""设备朝向与图像旋转。

ARKit 的 `capturedImage` **恒为 landscape-right 的原始缓冲**，
与设备实际怎么拿无关。若手机竖持，画面里的道路就是横躺的，
直接喂给 YOLOP 会显著降低分割质量——而且不会报错，只会安静地变差。

这里把"设备朝向"翻译成"需要顺时针旋转多少度才能让画面正立"。
"""

from __future__ import annotations

#: 设备界面朝向 -> 让画面正立所需顺时针旋转角度
_ROTATION = {
    "landscape_right": 0,
    "landscape_left": 180,
    "portrait": 90,
    "portrait_upside_down": 270,
}


def rotation_cw(device_orientation: str | None) -> int:
    """返回 0 / 90 / 180 / 270。未知或缺失时按 landscape_right 处理（不旋转）。"""
    if not device_orientation:
        return 0
    return _ROTATION.get(device_orientation, 0)


def inverse(rotation: int) -> int:
    """逆旋转角度，用于把模型输出转回原始朝向。"""
    return (360 - rotation) % 360
