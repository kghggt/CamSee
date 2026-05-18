# CamSee 监控系统 —— 开发日志

**项目名称：** CamSee 实时视频监控系统  
**开发日期：** 2026年05月16日  
**当前阶段：** 第一阶段完成（核心链路全线贯通）

---

## 📋 项目架构

```
┌──────────────┐    WebSocket     ┌──────────────────┐    WebSocket     ┌──────────────┐
│  USB 摄像头   │ ──── JPEG ────▶ │  云端服务器        │ ──── JPEG ────▶ │  浏览器/手机  │
│ (camera_client)│   (推流)       │ (FastAPI+uvicorn) │   (分发)        │  (Web 面板)   │
└──────────────┘                  └──────────────────┘                  └──────────────┘
       ↑                                  ↑                                    ↑
  本地电脑/开发板               Ubuntu 云服务器                          任意设备浏览器
  Tailscale 组网             Tailscale IP: <removed-ip>                 Tailscale 组网
```

---

## ✅ 已完成事项

### 1. 核心功能开发

| 模块 | 功能 | 状态 |
|------|------|------|
| 采集客户端 | OpenCV 采集 USB 摄像头视频帧 | ✅ 完成 |
| 采集客户端 | 自动扫描并交互式选择摄像头（内置/外接 USB） | ✅ 完成 |
| 采集客户端 | WebSocket 推流 + 断线自动重连（指数退避） | ✅ 完成 |
| 采集客户端 | 可配置分辨率、帧率、JPEG 压缩率 | ✅ 完成 |
| 云端服务器 | FastAPI 接收推流 + 多客户端并行广播 | ✅ 完成 |
| 云端服务器 | Token 认证机制 | ✅ 完成 |
| 云端服务器 | 摄像头名称智能路由（动态映射） | ✅ 完成 |
| Web 面板 | 暗色主题 Glassmorphism 设计 | ✅ 完成 |
| Web 面板 | 实时画面渲染（Canvas）+ FPS 监测 | ✅ 完成 |
| Web 面板 | 全屏切换（快捷键 F）、一键截图（快捷键 S） | ✅ 完成 |
| Web 面板 | 连接状态、摄像头信息、系统日志实时展示 | ✅ 完成 |

### 2. 网络环境攻坚

| 问题 | 解决方案 | 状态 |
|------|----------|------|
| 云服务器 8000 端口未开放 | SSH 本地端口映射 (`ssh -L`) 绕行 | ✅ 已解决 |
| 本地 8000 端口被占用 | 改用 8080 端口映射 | ✅ 已解决 |
| 前端与后端摄像头名称不匹配导致断连 | 服务端智能路由自动映射 | ✅ 已解决 |
| 校园网防火墙拦截校外访问 | 部署 Tailscale 虚拟局域网 | ✅ 已解决 |
| VPN 带宽不足导致画面卡顿 | 弱网参数优化 + Tailscale P2P 直连 | ✅ 已解决 |

### 3. 性能优化

| 优化项 | 优化前 | 优化后 |
|--------|--------|--------|
| 帧广播方式 | 串行逐个发送（一个慢全部卡） | 并行发送 + 0.5s 超时丢帧 |
| 前端渲染 | 每帧创建 Blob+Image 对象（频繁 GC） | 复用 Image 对象，减少内存抖动 |
| 状态轮询 | 每 3 秒发 2 次 HTTP 请求 | 每 5 秒发 1 次，延迟改用时间戳计算 |
| 弱网适配 | 1280×720 / 15fps / 质量70 | 640×360 / 20fps / 质量40 |

### 4. Tailscale 虚拟局域网部署

已在云服务器上完成 Tailscale 安装与配置：
- **服务器 Tailscale IP：** `<removed-ip>`
- **监控服务端口：** `8000`
- **效果：** 任何加入同一 Tailscale 网络的设备，无论身处何种网络环境（校园网、家庭宽带、5G），均可通过 `http://<removed-ip>:8000` 直接访问监控画面。

---

## 📁 项目文件结构

```
CamSee/
├── client/                     # 本地采集客户端
│   ├── camera_client.py        # 摄像头采集 + WebSocket 推流
│   ├── config.yaml             # 客户端配置（服务器地址、画质参数等）
│   └── requirements.txt        # Python 依赖
├── server/                     # 云端服务
│   ├── server.py               # FastAPI 主服务
│   ├── requirements.txt        # Python 依赖
│   ├── templates/
│   │   └── index.html          # 监控面板 HTML
│   └── static/
│       ├── css/style.css       # 面板样式
│       └── js/app.js           # 前端交互逻辑
├── deploy.bat                  # 一键部署脚本
├── Phase1_Summary.md           # 本文档
└── README.md                   # 项目说明
```

---

## 🔧 关键配置信息

| 配置项 | 值 |
|--------|----|
| 云服务器 SSH | `ssh -p <removed-port> ubuntu@<removed-host>` (密码: <removed>) |
| 云服务器 Tailscale IP | `<removed-ip>` |
| 监控服务端口 | `8000` |
| 认证 Token | `<removed-token>` |
| 客户端 Python 环境 | Conda `CamSee` (Python 3.12) |
| 服务端代码路径 | `/data1/lm/CamSee/server/` |
| 当前画质配置 | 640×360 / 20 FPS / JPEG 质量 40 |

---

## 🚧 后续开发计划

### 阶段 2-A：硬件 IoT 化（脱离个人电脑）
- [ ] 购置独立开发板（树莓派 / 香橙派 / ESP32-CAM）
- [ ] 将 `camera_client.py` 移植到开发板，或用 C++ 重写推流逻辑
- [ ] 实现开机自启动，插电即用

### 阶段 2-B：多端 Tailscale 组网
- [ ] 本地电脑安装 Tailscale 客户端
- [ ] 手机安装 Tailscale 客户端
- [ ] 开发板安装 Tailscale 客户端
- [ ] 全部设备加入同一网络，彻底摆脱校园网/VPN 限制

### 阶段 2-C：服务端高级功能
- [ ] **云端录像：** 滚动保存最近 24 小时画面（预留接口已就绪）
- [ ] **AI 动作检测：** 接入 YOLOv8 人形/运动检测，异常推送告警
- [ ] **多路摄像头：** 前端四宫格多路画面同时播放
- [ ] **安全加固：** 修改默认 Token，配置 HTTPS/WSS 加密传输

---

## 🖥️ 系统启动方法（速查）

### 第一步：启动云端服务器

通过 SSH 登录云服务器：
```bash
ssh -p <removed-port> ubuntu@<removed-host>
# 密码: <removed>
```

进入项目目录并启动服务：
```bash
cd /data1/lm/CamSee/server
python3 server.py
```
> 看到 `🚀 CamSee 服务器启动: http://0.0.0.0:8000` 即表示启动成功。

### 第二步：启动本地采集客户端

打开终端（PowerShell），激活 conda 环境并运行客户端：
```bash
conda activate CamSee
cd d:\My_project\CamSee\client
python camera_client.py
```
> 程序会自动扫描摄像头，选择编号后开始推流。
> 看到 `✅ 已连接，开始推流...` 即表示推流成功。

### 第三步：观看监控画面

在任何已加入 Tailscale 网络的设备上，打开浏览器访问：

| 场景 | 访问地址 |
|------|----------|
| 通过 Tailscale（推荐） | `http://<removed-ip>:8000` |
| 在校园网内（本地隧道方式） | 先运行 `ssh -g -N -L 8080:127.0.0.1:8000 -p <removed-port> ubuntu@<removed-host>`，然后访问 `http://127.0.0.1:8080` |

### ⚠️ 注意事项
- 云服务器上的 **Tailscale 需要保持运行**，如果服务器重启了，需要重新执行 `sudo tailscale up`。
- 本地电脑和手机需要安装并登录 **同一个 Tailscale 账号** 才能通过 `100.x.x.x` 地址访问。
- 如果画面卡顿，可以在 `client/config.yaml` 中降低 `fps`、`width`、`height` 或 `jpeg_quality` 的值。
- 如果摄像头选择了外接 USB（编号 1），但连接不稳定，可以尝试换一个 USB 口或检查拓展坞供电。
