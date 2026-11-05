#!/bin/bash
# 双击查看当前运行状态。
cd "$(dirname "$0")" || exit 1
clear
echo "=============================================="
echo "   山区公路落石监测系统 · 状态"
echo "=============================================="
echo
bash scripts/status.sh
echo
echo "按任意键关闭此窗口…"
[ -t 0 ] && read -n 1 -s
