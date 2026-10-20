# CamSee 监控系统

USB 摄像头 → 云服务器 → 浏览器实时查看

## 📁 项目结构

```
CamSee/
├── client/                       # 本地采集端（运行在电脑或开发板上）
│   ├── camera_client.py          # Windows 版采集 & 推流
│   ├── camera_client_imx6u.py    # i.MX6U 开发板版（V4L2）
│   ├── config.yaml               # 本地配置（不入库）
│   ├── config.yaml.example       # 配置模板
│   └── requirements.txt
│
├── server/                       # 云端服务（部署在云服务器上）
│   ├── server.py                 # FastAPI 服务主程序（含登录鉴权）
│   ├── requirements.txt
│   ├── static/                   # 前端静态资源
│   └── templates/                # HTML 模板
│
├── deploy.bat                    # 一键上传脚本
└── README.md
```

---

## 🚀 部署步骤

### 第一步：部署云端服务（Ubuntu 云服务器）

```bash
# 1. 上传 server 文件夹到云服务器（会得到 /opt/camsee/server）
scp -r server/ root@你的服务器IP:/opt/camsee/

# 2. SSH 登录云服务器
ssh root@你的服务器IP

# 3. 安装 Python 依赖（注意要进到 server 目录）
cd /opt/camsee/server
pip3 install -r requirements.txt

# 4. 开放防火墙端口
ufw allow 8000

# 5. 设置访问令牌并启动
export CAMSEE_TOKEN='换成一个足够长的随机串'
python3 server.py
```

**关于令牌：** 不再写在 `server.py` 里。请用环境变量 `CAMSEE_TOKEN` 提供，它同时用于
① 摄像头推流认证 ② 浏览器登录。若不设置，服务会在启动时随机生成一个并打印到日志
（重启即失效，仅适合临时调试）。

**后台运行（推荐）：**
```bash
# 使用 nohup
CAMSEE_TOKEN='你的令牌' nohup python3 server.py > camsee.log 2>&1 &

# 或使用 screen
screen -S camsee
export CAMSEE_TOKEN='你的令牌'
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

# 3. 复制配置模板并按需修改
copy config.yaml.example config.yaml
#    - server_url: 改成 ws://你的服务器IP:8000
#    - token: 改成和服务器 CAMSEE_TOKEN 一致的令牌
```

### 第三步：启动

```bash
# 1. 确保云服务器已启动（第一步）

# 2. 本地运行客户端
python camera_client.py

# 3. 浏览器打开监控面板
# 访问: http://你的服务器IP:8000
# 首次会看到登录页，输入 CAMSEE_TOKEN 的值即可进入
```

---

## ⚙️ 配置说明

### config.yaml（本地客户端）

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `server_url` | 云服务器 WebSocket 地址 | `ws://YOUR_SERVER_IP:8000` |
| `token` | 认证令牌（与服务器 `CAMSEE_TOKEN` 一致） | 无，必须自行填写 |
| `camera_id` | USB 摄像头编号 | `0` |
| `camera_name` | 摄像头名称 | `主摄像头` |
| `width` | 分辨率宽度 | `1280` |
| `height` | 分辨率高度 | `720` |
| `fps` | 帧率 | `15` |
| `jpeg_quality` | JPEG 质量 (1-100) | `70` |

### 环境变量（云服务器）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `CAMSEE_TOKEN` | 访问令牌（推流认证 + 登录），**必须与客户端 `token` 一致** | 未设置时启动期随机生成并打印 |
| `CAMSEE_HOST` | 监听地址 | `0.0.0.0` |
| `CAMSEE_PORT` | 监听端口 | `8000` |

---

## ⌨️ 快捷键

| 按键 | 功能 |
|------|------|
| `F` | 全屏 / 退出全屏 |
| `S` | 截图保存 |

---

## 🔒 安全建议

**v2 已内置：**

- 令牌改由环境变量 `CAMSEE_TOKEN` 注入，不再硬编码在源码里
- 浏览器 / API / 观看端 WebSocket 全部需要登录（HttpOnly + SameSite 会话 Cookie，30 天有效）
- 令牌比较使用 `hmac.compare_digest`，避免时序侧信道

**仍需自行处理：**

1. **轮换令牌**：如果旧令牌曾提交到仓库或分享过，请立刻更换 `CAMSEE_TOKEN`
2. **清理 git 历史**：早期提交里可能残留密钥，需要重写历史才能真正移除
3. **配置 HTTPS**：当前仍是明文 WS/HTTP，生产环境建议 Nginx 反向代理 + Let's Encrypt，把 WS 升级为 WSS
4. **限制端口访问**：用云服务器安全组限制 8000 端口的来源 IP
5. **定期轮换会话**：访问 `/logout` 可清除当前设备的登录状态

---

## 🗺️ 后续扩展

- [ ] 录像功能（接口已预留）
- [ ] 多摄像头支持（接口已预留）
- [ ] 移动端适配优化
- [ ] Nginx + HTTPS 部署配置
- [ ] 动作检测报警
