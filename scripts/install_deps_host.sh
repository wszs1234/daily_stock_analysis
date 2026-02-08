#!/bin/bash
# 在宿主机 Python 3.11 venv 中安装依赖（绕过 numpy/pandas 编译）
# 用法: ./scripts/install_deps_host.sh

set -e
cd "$(dirname "$0")/.."

echo "=== 1. 先安装 numpy/pandas（使用预编译 wheel，不触发编译） ==="
pip install numpy==1.26.4 pandas==2.1.4 --only-binary :all: -i https://mirrors.aliyun.com/pypi/simple/ --default-timeout=300

echo "=== 2. 再安装其余依赖 ==="
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --default-timeout=300

echo "=== 安装完成 ==="
