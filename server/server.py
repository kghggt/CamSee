"""
CamSee - 云端监控服务器

接收摄像头客户端推送的视频帧，通过 WebSocket 分发给浏览器观看者。
提供 Web 监控面板。
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Dict, Set
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request

# ============================================
# 配置
# ============================================
AUTH_TOKEN = "<removed-token>"
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8000

# 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("CamSee-Server")


# ============================================
# 摄像头管理器
# ============================================
class CameraManager:
    """管理摄像头连接、帧缓存和观看者分发"""

    def __init__(self):
        self.cameras: Dict[str, dict] = {}          # camera_name -> info
        self.latest_frames: Dict[str, bytes] = {}   # camera_name -> jpeg bytes
        self.viewers: Dict[str, Set[WebSocket]] = {} # camera_name -> viewer websockets
        self.frame_counts: Dict[str, int] = {}
        self.last_frame_time: Dict[str, float] = {}

    def register_camera(self, camera_name: str, info: dict):
        """注册新的摄像头连接"""
        self.cameras[camera_name] = {
            **info,
            "connected_at": datetime.now().isoformat(),
            "status": "online",
        }
        self.frame_counts[camera_name] = 0
        if camera_name not in self.viewers:
            self.viewers[camera_name] = set()
        logger.info(f"📷 摄像头已注册: {camera_name}")

    def unregister_camera(self, camera_name: str):
        """注销摄像头连接"""
        if camera_name in self.cameras:
            self.cameras[camera_name]["status"] = "offline"
            logger.info(f"📷 摄像头已断开: {camera_name}")

    async def update_frame(self, camera_name: str, frame_data: bytes):
        """更新最新帧并广播给所有观看者（并行发送，超时丢帧）"""
        self.latest_frames[camera_name] = frame_data
        self.frame_counts[camera_name] = self.frame_counts.get(camera_name, 0) + 1
        self.last_frame_time[camera_name] = time.time()

        # 并行广播给所有观看者
        if camera_name in self.viewers and self.viewers[camera_name]:
            dead_viewers = set()

            async def safe_send(viewer):
                try:
                    # 每个观众最多等 0.5 秒，超时就丢弃这一帧
                    await asyncio.wait_for(viewer.send_bytes(frame_data), timeout=0.5)
                except Exception:
                    dead_viewers.add(viewer)

            # 所有观众同时发送，互不阻塞
            await asyncio.gather(
                *[safe_send(v) for v in list(self.viewers[camera_name])]
            )

            # 清理断开的连接
            self.viewers[camera_name] -= dead_viewers

        # ====== 预留录像接口 ======
        # 未来可以在这里添加录像功能:
        # await recording_manager.write_frame(camera_name, frame_data)

    def add_viewer(self, camera_name: str, ws: WebSocket):
        """添加观看者"""
        # 如果请求了默认名称但当前有其他摄像头在线，自动映射到在线的摄像头
        if camera_name not in self.cameras and len(self.cameras) > 0:
            camera_name = list(self.cameras.keys())[0]
            
        if camera_name not in self.viewers:
            self.viewers[camera_name] = set()
        self.viewers[camera_name].add(ws)
        # 记录一下这个 ws 实际绑定的 camera_name，方便移除
        ws.bound_camera = camera_name
        
        logger.info(
            f"👁 观看者已连接 [{camera_name}]，"
            f"当前 {len(self.viewers[camera_name])} 人"
        )

    def remove_viewer(self, camera_name: str, ws: WebSocket):
        """移除观看者"""
        # 使用绑定的实际 camera_name
        actual_camera = getattr(ws, 'bound_camera', camera_name)
        if actual_camera in self.viewers:
            self.viewers[actual_camera].discard(ws)
            logger.info(
                f"👁 观看者已断开 [{actual_camera}]，"
                f"当前 {len(self.viewers[actual_camera])} 人"
            )

    def get_status(self) -> dict:
        """获取所有摄像头状态信息"""
        cameras_list = []
        for name, info in self.cameras.items():
            cameras_list.append(
                {
                    "name": name,
                    "status": info.get("status", "unknown"),
                    "resolution": info.get("resolution", "unknown"),
                    "fps": info.get("fps", 0),
                    "connected_at": info.get("connected_at", ""),
                    "viewers": len(self.viewers.get(name, set())),
                    "frames": self.frame_counts.get(name, 0),
                }
            )

        # ====== 预留多摄像头接口 ======
        # 当前返回所有已注册的摄像头列表
        # 前端可以根据列表动态渲染多个画面
        return {"cameras": cameras_list}


# ============================================
# FastAPI 应用
# ============================================
manager = CameraManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"🚀 CamSee 服务器启动: http://{SERVER_HOST}:{SERVER_PORT}")
    yield
    logger.info("CamSee 服务器已关闭")


app = FastAPI(title="CamSee Monitor", lifespan=lifespan)

# 静态文件和模板
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ---------- HTTP 路由 ----------

@app.get("/")
async def index(request: Request):
    """监控面板首页"""
    template = templates.get_template("index.html")
    content = template.render()
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=content)


@app.get("/api/status")
async def get_status():
    """获取系统状态 API"""
    return manager.get_status()


# ---------- WebSocket 路由 ----------

@app.websocket("/ws/camera")
async def camera_websocket(ws: WebSocket):
    """
    摄像头客户端 WebSocket 入口
    - 验证 Token
    - 接收摄像头信息
    - 持续接收视频帧并分发
    """
    # 验证认证令牌
    auth_header = ws.headers.get("authorization", "")
    token = auth_header.replace("Bearer ", "")

    if token != AUTH_TOKEN:
        await ws.close(code=4001, reason="Unauthorized")
        return

    await ws.accept()
    camera_name = None

    try:
        # 第一条消息应为摄像头信息（JSON）
        info_msg = await ws.receive_text()
        info = json.loads(info_msg)

        if info.get("type") != "camera_info":
            await ws.close(code=4002, reason="Expected camera_info message")
            return

        camera_name = info.get("camera_name", "Unknown")
        manager.register_camera(camera_name, info)

        # 持续接收视频帧
        while True:
            frame_data = await ws.receive_bytes()
            await manager.update_frame(camera_name, frame_data)

    except WebSocketDisconnect:
        logger.info(f"摄像头 {camera_name} 断开连接")
    except Exception as e:
        logger.error(f"摄像头处理错误: {e}")
    finally:
        if camera_name:
            manager.unregister_camera(camera_name)


@app.websocket("/ws/viewer")
async def viewer_websocket(
    ws: WebSocket,
    camera: str = Query(default="主摄像头"),
):
    """
    浏览器观看者 WebSocket 入口
    - 连接后立即推送最新帧
    - 持续接收来自摄像头的帧数据
    """
    await ws.accept()
    manager.add_viewer(camera, ws)

    try:
        # 如果有最新帧，立即发送一帧
        if camera in manager.latest_frames:
            await ws.send_bytes(manager.latest_frames[camera])

        # 保持连接，监听来自观看者的控制消息
        while True:
            msg = await ws.receive_text()
            # 预留：未来可处理观看者发送的命令
            # 例如: 截图、云台控制等

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"观看者处理错误: {e}")
    finally:
        manager.remove_viewer(camera, ws)


# ============================================
# 启动入口
# ============================================
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)
