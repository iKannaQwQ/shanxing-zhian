#!/bin/bash
# 双击即可启动。关掉这个终端窗口不影响服务，服务在后台继续运行。
cd "$(dirname "$0")" || exit 1
clear
echo "=============================================="
echo "   山区公路落石监测系统 · 启动"
echo "=============================================="
echo

bash scripts/start_all.sh
status=$?

echo
if [ "$status" -ne 0 ]; then
  echo "启动失败。日志在 data/logs/ 目录。"
  echo "按任意键关闭…"
  [ -t 0 ] && read -n 1 -s
  exit 1
fi

open "http://127.0.0.1:8000/" 2>/dev/null \
  && echo "浏览器已打开；若没弹出，请手动访问上面显示的地址。"

echo
echo "服务在后台运行，关闭这个窗口不会停止它。"
echo "要停止请双击「停止系统.command」。"
echo
echo "按任意键关闭此窗口…"
[ -t 0 ] && read -n 1 -s
