"""
CamSee - 云端监控服务器

接收摄像头客户端推送的视频帧，通过 WebSocket 分发给浏览器观看者。
提供 Web 监控面板。

v2 安全模型：
  - 令牌只从环境变量 CAMSEE_TOKEN 读取；未设置时在启动期随机生成并打印（不落盘、不入库）
  - 浏览器访问 / 需先提交令牌换取 HttpOnly 签名 Cookie
  - /api/status、/ws/viewer 校验 Cookie；/ws/camera 校验 Authorization: Bearer

v2 分发模型：
  - 每个观看者一条有界队列（只留最新帧），发送在独立任务中进行
  - 摄像头推送路径不再等待观看者，慢观众只会丢自己的帧，不会拖慢整条链路
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ============================================
# 配置
# ============================================
BASE_DIR = Path(__file__).resolve().parent

# 令牌不再硬编码。请通过环境变量注入：export CAMSEE_TOKEN='你的自定义密钥'
AUTH_TOKEN = os.environ.get("CAMSEE_TOKEN", "").strip()
EPHEMERAL_TOKEN = not AUTH_TOKEN
if EPHEMERAL_TOKEN:
    # 未配置时随机生成，保证默认可用且每次启动都不同
    AUTH_TOKEN = secrets.token_urlsafe(24)

SERVER_HOST = os.environ.get("CAMSEE_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("CAMSEE_PORT", "8000"))

COOKIE_NAME = "camsee_session"
COOKIE_MAX_AGE = 30 * 24 * 3600          # 30 天
VIEWER_QUEUE_SIZE = 2                    # 每个观看者最多积压 2 帧，超出丢旧帧

# 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("CamSee-Server")


# ============================================
# 鉴权工具
# ============================================
def _session_value() -> str:
    """由令牌派生出的会话 Cookie 值（不直接暴露令牌本身）"""
    return hmac.new(AUTH_TOKEN.encode(), b"camsee-session-v1", hashlib.sha256).hexdigest()


def check_token(candidate: Optional[str]) -> bool:
    """恒定时间比较，避免时序侧信道"""
    return bool(candidate) and hmac.compare_digest(candidate, AUTH_TOKEN)


def is_authenticated(conn) -> bool:
    """校验会话 Cookie（比对派生值，不比对令牌本身）。Request 与 WebSocket 共用"""
    cookie = conn.cookies.get(COOKIE_NAME, "")
    return bool(cookie) and hmac.compare_digest(cookie, _session_value())


LOGIN_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CamSee 登录</title>
<style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{min-height:100vh;display:flex;align-items:center;justify-content:center;
        background:#0a0e17;color:#f1f5f9;
        font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif}}
  .card{{width:340px;padding:32px;border:1px solid rgba(99,102,241,.25);border-radius:16px;
         background:rgba(17,24,39,.85)}}
  h1{{font-size:20px;font-weight:600;margin-bottom:6px}}
  p.sub{{font-size:13px;color:#94a3b8;margin-bottom:22px}}
  input{{width:100%;padding:11px 13px;border-radius:8px;font-size:14px;
         border:1px solid rgba(99,102,241,.3);background:#0f1626;color:#f1f5f9;outline:none}}
  input:focus{{border-color:#6366f1}}
  button{{width:100%;margin-top:14px;padding:11px;border:0;border-radius:8px;cursor:pointer;
          font-size:14px;font-weight:500;color:#fff;background:#6366f1}}
  button:hover{{background:#818cf8}}
  .err{{margin-top:12px;font-size:13px;color:#ef4444}}
  .hint{{margin-top:18px;font-size:12px;color:#64748b;line-height:1.6}}
</style>
</head>
<body>
  <form class="card" method="post" action="/login">
    <h1>CamSee</h1>
    <p class="sub">请输入访问令牌</p>
    <input type="password" name="token" placeholder="访问令牌" autofocus autocomplete="current-password">
    <button type="submit">进入监控中心</button>
    {error}
    <div class="hint">令牌即服务器端环境变量 CAMSEE_TOKEN 的值。</div>
  </form>
</body>
</html>"""


# ============================================
# 摄像头管理器
# ============================================
class ViewerView:
    """单个观看者的接收队列与发送任务"""

    __slots__ = ("ws", "camera_name", "queue", "task")

    def __init__(self, ws: WebSocket, camera_name: str):
        self.ws = ws
        self.camera_name = camera_name
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=VIEWER_QUEUE_SIZE)
        self.task: Optional[asyncio.Task] = None


class CameraManager:
    """管理摄像头连接、帧缓存和观看者分发"""

    def __init__(self):
        self.cameras: Dict[str, dict] = {}                  # camera_name -> info
        self.latest_frames: Dict[str, bytes] = {}           # camera_name -> jpeg bytes
        self.viewers: Dict[str, Dict[WebSocket, ViewerView]] = {}
        self.frame_counts: Dict[str, int] = {}
        self.last_frame_time: Dict[str, float] = {}
        self._by_ws: Dict[WebSocket, ViewerView] = {}

    # ---------- 摄像头 ----------
    def register_camera(self, camera_name: str, info: dict):
        """注册新的摄像头连接"""
        self.cameras[camera_name] = {
            **info,
            "connected_at": datetime.now().isoformat(),
            "status": "online",
        }
        self.frame_counts[camera_name] = 0
        self.viewers.setdefault(camera_name, {})
        logger.info(f"📷 摄像头已注册: {camera_name}")

    def unregister_camera(self, camera_name: str):
        """注销摄像头连接：连同缓存一起移除，避免状态面板残留历史条目"""
        if camera_name in self.cameras:
            self.cameras.pop(camera_name, None)
            self.latest_frames.pop(camera_name, None)
            # 观看者连接保留，等摄像头重新上线后自动恢复画面
            logger.info(f"📷 摄像头已断开: {camera_name}")

    # ---------- 帧分发 ----------
    def update_frame(self, camera_name: str, frame_data: bytes):
        """
        更新最新帧并投递给所有观看者。

        关键点：本方法不等待任何网络 I/O。每个观看者一条有界队列，
        队列满则丢弃最旧帧。因此慢观众只影响自己，不会反压摄像头推流。
        """
        self.latest_frames[camera_name] = frame_data
        self.frame_counts[camera_name] = self.frame_counts.get(camera_name, 0) + 1
        self.last_frame_time[camera_name] = time.time()

        for viewer in self.viewers.get(camera_name, {}).values():
            if viewer.queue.full():
                try:
                    viewer.queue.get_nowait()      # 丢弃旧帧，保留实时性
                except asyncio.QueueEmpty:
                    pass
            try:
                viewer.queue.put_nowait(frame_data)
            except asyncio.QueueFull:
                pass

        # ====== 预留录像接口 ======
        # await recording_manager.write_frame(camera_name, frame_data)

    async def _pump(self, viewer: ViewerView):
        """独立发送任务：从队列取帧发给单个观看者"""
        try:
            while True:
                frame = await viewer.queue.get()
                await viewer.ws.send_bytes(frame)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.info(f"👁 观看者发送中断 [{viewer.camera_name}]: {e}")

    # ---------- 观看者 ----------
    def add_viewer(self, camera_name: str, ws: WebSocket) -> ViewerView:
        """添加观看者，并启动其独立发送任务"""
        # 如果请求的名称不存在但当前有摄像头在线，自动映射到在线的摄像头
        if camera_name not in self.cameras and len(self.cameras) > 0:
            camera_name = next(iter(self.cameras))

        viewer = ViewerView(ws, camera_name)
        self.viewers.setdefault(camera_name, {})[ws] = viewer
        self._by_ws[ws] = viewer
        viewer.task = asyncio.create_task(self._pump(viewer))

        logger.info(
            f"👁 观看者已连接 [{camera_name}]，"
            f"当前 {len(self.viewers[camera_name])} 人"
        )
        return viewer

    def remove_viewer(self, ws: WebSocket):
        """移除观看者并结束其发送任务"""
        viewer = self._by_ws.pop(ws, None)
        if viewer is None:
            return

        bucket = self.viewers.get(viewer.camera_name)
        if bucket is not None:
            bucket.pop(ws, None)
            logger.info(
                f"👁 观看者已断开 [{viewer.camera_name}]，"
                f"当前 {len(bucket)} 人"
            )

        if viewer.task is not None:
            viewer.task.cancel()

    def get_status(self) -> dict:
        """获取所有在线摄像头状态信息"""
        now = time.time()
        cameras_list = []
        for name, info in self.cameras.items():
            last = self.last_frame_time.get(name)
            cameras_list.append(
                {
                    "name": name,
                    "status": info.get("status", "unknown"),
                    "resolution": info.get("resolution", "unknown"),
                    "fps": info.get("fps", 0),
                    "connected_at": info.get("connected_at", ""),
                    "viewers": len(self.viewers.get(name, {})),
                    "frames": self.frame_counts.get(name, 0),
                    # 服务端最后一帧距今多久（毫秒），用于前端展示画面滞后
                    "frame_age_ms": int((now - last) * 1000) if last else None,
                }
            )
        return {"cameras": cameras_list}


# ============================================
# FastAPI 应用
# ============================================
manager = CameraManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if EPHEMERAL_TOKEN:
        logger.warning("=" * 62)
        logger.warning("未设置环境变量 CAMSEE_TOKEN，本次启动使用随机令牌：")
        logger.warning(f"    {AUTH_TOKEN}")
        logger.warning("客户端 config.yaml 的 token 需与此一致，重启后会变化。")
        logger.warning("生产环境请固定：export CAMSEE_TOKEN='你的自定义密钥'")
        logger.warning("=" * 62)
    logger.info(f"🚀 CamSee 服务器启动: http://{SERVER_HOST}:{SERVER_PORT}")
    yield
    logger.info("CamSee 服务器已关闭")


app = FastAPI(title="CamSee Monitor", lifespan=lifespan)

# 静态文件和模板（用绝对路径，不依赖启动时的工作目录）
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ---------- 鉴权路由 ----------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """监控面板首页；未登录时返回登录表单"""
    if not is_authenticated(request):
        return HTMLResponse(LOGIN_PAGE.format(error=""), status_code=401)

    content = templates.get_template("index.html").render()
    return HTMLResponse(content=content)


@app.post("/login")
async def login(request: Request, token: str = Form(default="")):
    """提交令牌换取会话 Cookie"""
    if not check_token(token):
        logger.warning(f"登录失败，来源 {request.client.host if request.client else '?'}")
        return HTMLResponse(
            LOGIN_PAGE.format(error='<div class="err">令牌不正确</div>'),
            status_code=401,
        )

    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        _session_value(),
        max_age=COOKIE_MAX_AGE,
        httponly=True,                       # 禁止 JS 读取
        samesite="lax",                      # 阻断跨站携带
        secure=request.url.scheme == "https",
        path="/",
    )
    logger.info(f"登录成功，来源 {request.client.host if request.client else '?'}")
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


# ---------- HTTP 路由 ----------

@app.get("/api/status")
async def get_status(request: Request):
    """获取系统状态 API（需登录）"""
    if not is_authenticated(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return manager.get_status()


# ---------- WebSocket 路由 ----------

@app.websocket("/ws/camera")
async def camera_websocket(ws: WebSocket):
    """
    摄像头客户端 WebSocket 入口
    - 验证 Token（Authorization: Bearer）
    - 接收摄像头信息
    - 持续接收视频帧并分发
    """
    auth_header = ws.headers.get("authorization", "")
    token = auth_header[7:] if auth_header.lower().startswith("bearer ") else ""

    if not check_token(token):
        await ws.close(code=4001, reason="Unauthorized")
        return

    await ws.accept()
    camera_name = None

    try:
        # 第一条消息应为摄像头信息（JSON）
        info = json.loads(await ws.receive_text())

        if info.get("type") != "camera_info":
            await ws.close(code=4002, reason="Expected camera_info message")
            return

        camera_name = info.get("camera_name", "Unknown")
        manager.register_camera(camera_name, info)

        # 持续接收视频帧
        while True:
            frame_data = await ws.receive_bytes()
            manager.update_frame(camera_name, frame_data)

    except WebSocketDisconnect:
        logger.info(f"摄像头 {camera_name} 断开连接")
    except Exception as e:
        logger.error(f"摄像头处理错误: {e}")
    finally:
        if camera_name:
            manager.unregister_camera(camera_name)


@app.websocket("/ws/viewer")
async def viewer_websocket(ws: WebSocket):
    """
    浏览器观看者 WebSocket 入口
    - 校验会话 Cookie（或 ?token= 参数，便于非浏览器客户端）
    - 连接后立即推送最新帧
    - 后续帧由独立发送任务推送
    """
    if not (is_authenticated(ws) or check_token(ws.query_params.get("token"))):
        await ws.close(code=4001, reason="Unauthorized")
        return

    await ws.accept()
    requested = ws.query_params.get("camera", "主摄像头")
    viewer = manager.add_viewer(requested, ws)

    try:
        # 用实际绑定的摄像头名取缓存帧，避免名称映射导致首帧丢失
        latest = manager.latest_frames.get(viewer.camera_name)
        if latest is not None:
            await ws.send_bytes(latest)

        # 保持连接，监听来自观看者的控制消息（预留：截图、云台控制等）
        while True:
            await ws.receive_text()

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"观看者处理错误: {e}")
    finally:
        manager.remove_viewer(ws)


# ============================================
# 启动入口
# ============================================
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)
