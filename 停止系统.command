#!/bin/bash
# 双击即可停止全部服务。
cd "$(dirname "$0")" || exit 1
clear
echo "=============================================="
echo "   山区公路落石监测系统 · 停止"
echo "=============================================="
echo
echo "提示：请先在手机 App 里点「停止」，再关这里。"
echo

bash scripts/stop_all.sh

echo
echo "按任意键关闭此窗口…"
[ -t 0 ] && read -n 1 -s
