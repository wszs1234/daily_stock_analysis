# 国内服务器部署指南（run_new.py）

本文档说明如何将 `run_new.py`（A股智能分析 Web 应用）部署到国内服务器，并通过外网 VPS 代理访问 Gemini API。

## 架构说明

```
用户浏览器
    ↓
国内服务器 124.202.158.181 (Streamlit)
    ↓ 代理流量
外网 VPS 199.180.117.13
    ↓
Gemini API (Google)
```

- **国内服务器**：运行 Streamlit 应用，供用户访问
- **外网 VPS**：作为代理出口，转发对 Gemini API 的请求

---

## 一、外网 VPS 代理配置

### 方案 A：SSH 隧道（推荐，无需在 VPS 安装额外软件）

在国内服务器上执行，通过 SSH 建立到 VPS 的动态端口转发，形成本地 SOCKS5 代理：

```bash
# 1. 在国内服务器生成 SSH 密钥（如无）
ssh-keygen -t ed25519 -f ~/.ssh/vps_proxy -N ""

# 2. 将公钥添加到 VPS 的 ~/.ssh/authorized_keys
ssh-copy-id -i ~/.ssh/vps_proxy.pub root@199.180.117.13

# 3. 建立 SOCKS5 代理隧道（端口 10808）
# -D 10808: 本地监听 10808 作为 SOCKS5 代理
# -f: 后台运行  -C: 压缩  -q: 静默  -N: 不执行远程命令
ssh -D 10808 -f -C -q -N -i ~/.ssh/vps_proxy root@199.180.117.13

# 4. 验证代理
curl -x socks5://127.0.0.1:10808 https://www.google.com -I
```

**保持隧道常驻（systemd 服务）**：

```bash
# 创建 /etc/systemd/system/ssh-proxy-tunnel.service
sudo tee /etc/systemd/system/ssh-proxy-tunnel.service << 'EOF'
[Unit]
Description=SSH SOCKS5 Proxy Tunnel to VPS
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/ssh -D 10808 -o ServerAliveInterval=60 -o ExitOnForwardFailure=yes -N -i /root/.ssh/vps_proxy root@199.180.117.13
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ssh-proxy-tunnel
sudo systemctl start ssh-proxy-tunnel
```

### 方案 B：在 VPS 上运行 HTTP 代理

如需 HTTP 代理而非 SOCKS5，可在 VPS 上安装 tinyproxy：

```bash
# 在 VPS (199.180.117.13) 上执行
sudo apt update && sudo apt install -y tinyproxy

# 编辑配置，允许国内服务器 IP 连接
sudo sed -i 's/^Allow 127.0.0.1/Allow 127.0.0.1\nAllow 124.202.158.181/' /etc/tinyproxy/tinyproxy.conf
sudo systemctl restart tinyproxy
```

国内服务器 `.env` 中配置：

```
GEMINI_PROXY_URL=http://199.180.117.13:8888
```

> 默认 tinyproxy 端口为 8888，可按需修改 `/etc/tinyproxy/tinyproxy.conf` 中的 `Port`。

---

## 二、国内服务器环境准备

### 1. 安装依赖

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip git

# 创建项目目录
sudo mkdir -p /opt/stock-analyzer
sudo chown $USER:$USER /opt/stock-analyzer
cd /opt/stock-analyzer
```

### 2. 克隆/上传代码

```bash
git clone <你的仓库地址> .
# 或使用 scp/rsync 上传项目文件
```

### 3. 创建虚拟环境并安装依赖

```bash
python3.11 -m venv venv
source venv/bin/activate

# 安装 run_new.py 所需依赖
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
pip install streamlit google-genai
```

### 4. 配置环境变量

```bash
cp .env.example .env
vim .env
```

**必须配置项**：

```ini
# Gemini API（从 https://aistudio.google.com/ 获取）
GEMINI_API_KEY=your_gemini_api_key

# 代理（方案 A 使用 SSH 隧道时）
GEMINI_PROXY_URL=socks5://127.0.0.1:10808

# 若使用方案 B（VPS 上的 HTTP 代理）
# GEMINI_PROXY_URL=http://199.180.117.13:8888

# 用户认证与用量监控（默认开启，本地调试可设 AUTH_REQUIRED=false）
AUTH_REQUIRED=true
USER_DB_PATH=./data/users.db
USAGE_DB_PATH=./data/usage.db
```

---

## 三、启动应用

### 前台测试

```bash
cd /opt/stock-analyzer
source venv/bin/activate
streamlit run run_new.py --server.port 8501 --server.address 0.0.0.0
```

浏览器访问：`http://124.202.158.181:8501`

### 使用 systemd 常驻运行

```bash
sudo tee /etc/systemd/system/stock-analyzer-web.service << 'EOF'
[Unit]
Description=A股智能分析 Web 应用 (run_new.py)
After=network.target ssh-proxy-tunnel.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/stock-analyzer
Environment="PATH=/opt/stock-analyzer/venv/bin"
ExecStart=/opt/stock-analyzer/venv/bin/streamlit run run_new.py --server.port 8501 --server.address 0.0.0.0 --server.headless true
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable stock-analyzer-web
sudo systemctl start stock-analyzer-web
```

### 防火墙放行端口

```bash
sudo ufw allow 8501/tcp
sudo ufw reload
```

---

## 四、Nginx 反向代理（可选）

如需使用 80/443 和域名：

```nginx
server {
    listen 80;
    server_name your-domain.com;
    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
    }
}
```

---

## 五、用量监控说明

- 用户注册信息保存在 `./data/users.db`，用量记录保存在 `./data/usage.db`
- 每次「开始深度分析」及多轮追问的 Gemini 调用都会计入用量
- 查看用量报表：

```bash
cd /opt/stock-analyzer
source venv/bin/activate
python scripts/usage_report.py
```

---

## 六、安全检查建议

1. **限制 VPS 访问**：仅允许国内服务器 IP `124.202.158.181` 连接代理端口
2. **HTTPS**：生产环境建议配置 Let's Encrypt 证书启用 HTTPS
3. **强密码**：用户注册时要求强密码，管理员定期检查 `users` 表
4. **日志**：定期查看 `./logs/` 和应用日志，排查异常请求
