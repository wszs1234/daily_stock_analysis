#!/bin/bash
# 在服务器上运行此脚本部署 run_new.py
set -e
cd /opt/stock-analyzer

echo "=== 1. 停止并删除旧容器 ==="
docker stop stock-run-new 2>/dev/null || true
docker rm stock-run-new 2>/dev/null || true

echo "=== 2. 构建镜像 ==="
docker build -f docker/Dockerfile.run_new -t stock-run-new .

echo "=== 3. 启动容器（增加线程限制） ==="
docker run -d \
  --name stock-run-new \
  --restart unless-stopped \
  --network host \
  --ulimit nproc=65535:65535 \
  --ulimit nofile=65535:65535 \
  -v /opt/stock-analyzer/data:/app/data \
  -v /opt/stock-analyzer/.env:/app/.env \
  -e TZ=Asia/Shanghai \
  stock-run-new

echo "=== 4. 等待启动 ==="
sleep 5
docker logs stock-run-new --tail 20

echo ""
echo "=== 部署完成，访问 http://YOUR_IP:8501 ==="
