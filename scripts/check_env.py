#!/usr/bin/env python3
"""环境自检：只用标准库，任何 Python 3.9+ 都能跑。

用法:
    python3 scripts/check_env.py
    python3 scripts/check_env.py --json
    python3 scripts/check_env.py --strict     # 缺可选依赖也算失败
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / ".tools"
NODE_BIN = TOOLS / "node" / "bin"
VENV_BIN = TOOLS / "venv" / "bin"


def run(cmd: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except FileNotFoundError:
        return 127, "not found"
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def find(name: str, extra: list[Path] | None = None) -> str | None:
    for d in extra or []:
        cand = d / name
        if cand.exists():
            return str(cand)
    return shutil.which(name)


def first_line(s: str) -> str:
    return s.splitlines()[0] if s else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    rows: list[dict] = []

    def add(name: str, ok: bool, detail: str, hint: str = "", required: bool = True) -> None:
        rows.append({"name": name, "ok": ok, "detail": detail, "hint": hint, "required": required})

    # 平台
    add("os", True, f"{platform.system()} {platform.release()} ({platform.machine()})")

    # 系统 python
    rc, out = run([sys.executable, "--version"])
    add("python(runner)", rc == 0, first_line(out) or sys.version.split()[0],
        "建议用 .tools/venv/bin/python 运行本脚本")

    # 工具 venv
    vpy = VENV_BIN / "python"
    if vpy.exists():
        rc, out = run([str(vpy), "--version"])
        add("python(tools)", rc == 0, first_line(out))
        rc, out = run([str(vpy), "-c", "import json,sys;print(sys.version_info[:2])"])
        major_minor = out if rc == 0 else "?"
        add("tools.venv==3.11", rc == 0 and "3, 11" in major_minor, major_minor,
            "uv venv --python 3.11 .tools/venv")
    else:
        add("python(tools)", False, "缺失", "bash scripts/bootstrap_mac.sh")

    # cmake
    cmake = find("cmake", [VENV_BIN])
    if cmake:
        rc, out = run([cmake, "--version"])
        add("cmake", rc == 0, first_line(out))
    else:
        add("cmake", False, "缺失", "bash scripts/bootstrap_mac.sh（提供 Windows 侧需另装 CMake + MSVC）")

    # jsonschema
    if vpy.exists():
        rc, out = run([str(vpy), "-c", "import importlib.metadata as m;print(m.version('jsonschema'))"])
        add("jsonschema", rc == 0, out if rc == 0 else "缺失", "uv pip install jsonschema")

    # node / npm
    node = find("node", [NODE_BIN])
    if node:
        rc, out = run([node, "--version"])
        add("node", rc == 0, first_line(out))
    else:
        add("node", False, "缺失", "bash scripts/bootstrap_mac.sh（Windows 需装 Node 22/24 LTS）")

    npm = find("npm", [NODE_BIN])
    if npm:
        env_path = f"{Path(node).parent}:{os.environ.get('PATH', '')}" if node else os.environ.get("PATH", "")
        try:
            proc = subprocess.run([npm, "--version"], capture_output=True, text=True,
                                  timeout=20, env={**os.environ, "PATH": env_path})
            add("npm", proc.returncode == 0, first_line(proc.stdout.strip()))
        except (OSError, subprocess.SubprocessError) as e:
            add("npm", False, str(e))
    else:
        add("npm", False, "缺失", "与 node 一同安装")

    # git
    git = find("git")
    add("git", bool(git), run([git, "--version"])[1] if git else "缺失", "安装 Git")

    # iOS 工具链（可选项，仅采集端阶段需要）
    if platform.system() == "Darwin":
        rc, out = run(["xcodebuild", "-version"])
        add("xcodebuild", rc == 0, first_line(out) if rc == 0 else "缺失",
            "安装 Xcode（仅 iOS 采集端需要）", required=False)
        rc, out = run(["xcrun", "simctl", "list", "runtimes"])
        has_rt = rc == 0 and "iOS" in out
        add("ios.simulator", has_rt, "已安装" if has_rt else "无 iOS 模拟器运行时",
            "ARKit/LiDAR 不支持模拟器，必须用真机；此项仅影响 SwiftUI 预览调试", required=False)

    # 契约可校验性
    if vpy.exists() and (ROOT / "contracts" / "validate.py").exists():
        rc, out = run([str(vpy), str(ROOT / "contracts" / "validate.py"), "--all"], timeout=60)
        add("contracts", rc == 0, first_line(out.splitlines()[-1]) if out else "",
            "python contracts/validate.py --all")

    failed = [r for r in rows if not r["ok"] and (r["required"] or args.strict)]

    if args.json:
        print(json.dumps({"ok": not failed, "checks": rows}, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            mark = "OK  " if r["ok"] else ("MISS" if r["required"] else "WARN")
            print(f"[{mark}] {r['name']:<16} {r['detail']}")
        if failed:
            print("\n需要处理：")
            for r in failed:
                print(f"  - {r['name']}: {r['hint'] or '见 README'}")
        else:
            print("\n环境就绪（可选告警不影响后端/感知/风险引擎开发）。")

    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
