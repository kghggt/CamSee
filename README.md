# CamSee 监控系统

USB 摄像头 → 云服务器 → 浏览器实时查看

## 📁 项目结构

```
CamSee/
├── client/                    # 本地采集端（运行在你的电脑上）
│   ├── camera_client.py       # 摄像头采集 & 推流
│   ├── config.yaml            # 配置文件
│   └── requirements.txt
│
├── server/                    # 云端服务（部署在云服务器上）
│   ├── server.py              # FastAPI 服务主程序
│   ├── requirements.txt
│   ├── static/                # 前端静态资源
│   └── templates/             # HTML 模板
│
└── README.md
```

---

## 🚀 部署步骤

### 第一步：部署云端服务（Ubuntu 云服务器）

```bash
# 1. 上传 server 文件夹到云服务器
scp -r server/ root@你的服务器IP:/opt/camsee/

# 2. SSH 登录云服务器
ssh root@你的服务器IP

# 3. 安装 Python 依赖
cd /opt/camsee
pip3 install -r requirements.txt

# 4. 修改认证令牌（server.py 第 15 行，改成你自己的）
nano server.py
# 修改: AUTH_TOKEN = "你的自定义密钥"

# 5. 开放防火墙端口
ufw allow 8000

# 6. 启动服务
python3 server.py
```

**后台运行（推荐）：**
```bash
# 使用 nohup
nohup python3 server.py > camsee.log 2>&1 &

# 或使用 screen
screen -S camsee
python3 server.py
# Ctrl+A, D 分离 screen
```

### 第二步：配置本地客户端（你的 Windows 电脑）

```bash
# 1. 创建 Python 虚拟环境
cd client
python -m venv venv
venv\Scripts\activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 修改配置文件 config.yaml
#    - server_url: 改成 ws://你的服务器IP:8000
#    - token: 改成和服务器一致的令牌
```

### 第三步：启动

```bash
# 1. 确保云服务器已启动（第一步）

# 2. 本地运行客户端
python camera_client.py

# 3. 浏览器打开监控面板
# 访问: http://你的服务器IP:8000
```

---

## ⚙️ 配置说明

### config.yaml（本地客户端）

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `server_url` | 云服务器 WebSocket 地址 | `ws://YOUR_SERVER_IP:8000` |
| `token` | 认证令牌 | `<removed-token>` |
| `camera_id` | USB 摄像头编号 | `0` |
| `camera_name` | 摄像头名称 | `主摄像头` |
| `width` | 分辨率宽度 | `1280` |
| `height` | 分辨率高度 | `720` |
| `fps` | 帧率 | `15` |
| `jpeg_quality` | JPEG 质量 (1-100) | `70` |

### server.py（云服务器）

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `AUTH_TOKEN` | 认证令牌 | `<removed-token>` |
| `SERVER_HOST` | 监听地址 | `0.0.0.0` |
| `SERVER_PORT` | 监听端口 | `8000` |

---

## ⌨️ 快捷键

| 按键 | 功能 |
|------|------|
| `F` | 全屏 / 退出全屏 |
| `S` | 截图保存 |

---

## 🔒 安全建议

1. **修改默认 Token**：请务必修改 `AUTH_TOKEN` 和 `config.yaml` 中的 `token`
2. **配置 HTTPS**：生产环境建议使用 Nginx 反向代理 + Let's Encrypt SSL
3. **限制端口访问**：使用云服务器安全组限制 8000 端口的访问 IP

---

## 🗺️ 后续扩展

- [ ] 录像功能（接口已预留）
- [ ] 多摄像头支持（接口已预留）
- [ ] 移动端适配优化
- [ ] Nginx + HTTPS 部署配置
- [ ] 动作检测报警
