"""
CamSee - 嵌入式摄像头采集客户端
专为 正点原子 i.MX6U-Mini 开发板（ARM Linux）定制

主要特点（相比 Windows 版）：
  - 使用 V4L2 后端（Linux 通用摄像头驱动框架），不依赖 DirectShow
  - 摄像头设备路径使用 /dev/videoX 形式，而非 Windows 的数字索引
  - 针对 ARM 低功耗处理器进行了性能优化：
      * 发送队列限制（防止 CPU 积压导致的卡顿）
      * 异步帧捕获与推流解耦（避免捕获阻塞发送）
  - 去除了 Windows 专属的 CAP_DSHOW 扫描逻辑
  - 使用 SIGTERM/SIGINT 统一信号处理，兼容 Linux systemd 自启

依赖安装（在开发板上执行）：
  sudo apt-get update
  sudo apt-get install -y python3-pip python3-opencv
  pip3 install websockets pyyaml

运行方式：
  python3 camera_client_imx6u.py
  # 或者指定设备和配置文件：
  python3 camera_client_imx6u.py --device /dev/video1 --config config.yaml
"""

import asyncio
import cv2
import websockets
import inspect
import json
import time
import yaml
import logging
import signal
import sys
import os
import argparse
from pathlib import Path

# ============================================================
# 命令行参数解析
# ============================================================
def parse_args():
    parser = argparse.ArgumentParser(description="CamSee 嵌入式客户端 (i.MX6U)")
    parser.add_argument(
        "--device",
        type=str,
        default=None,
        help="摄像头设备节点，如 /dev/video0（不指定则自动检测）",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=str(Path(__file__).parent / "config.yaml"),
        help="配置文件路径（默认：同目录下的 config.yaml）",
    )
    parser.add_argument(
        "--list-cameras",
        action="store_true",
        help="仅列出可用的摄像头设备后退出",
    )
    return parser.parse_args()


# ============================================================
# 配置加载
# ============================================================
def load_config(config_path: str) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def ws_auth_kwargs(token: str) -> dict:
    """
    生成携带认证头的 connect() 参数。

    websockets 14+ 将顶层 connect() 切换到新的 asyncio 实现，握手头参数
    由 extra_headers 改名为 additional_headers。开发板上往往是旧版本，
    这里按实际签名选择，避免因版本差异直接抛 TypeError。
    """
    params = inspect.signature(websockets.connect).parameters
    key = "additional_headers" if "additional_headers" in params else "extra_headers"
    return {key: {"Authorization": f"Bearer {token}"}}


# ============================================================
# 摄像头设备扫描（Linux V4L2 方式）
# ============================================================
def get_available_cameras() -> list:
    """
    扫描 /dev/video* 设备节点，返回实际可读取帧的设备列表。
    返回格式：["/dev/video0", "/dev/video2", ...]
    """
    devices = sorted([
        f"/dev/{d}"
        for d in os.listdir("/dev")
        if d.startswith("video")
    ])

    available = []
    for dev in devices:
        # 使用 V4L2 后端打开，速度快、不依赖 X11 窗口
        cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
        if cap.isOpened():
            ret, _ = cap.read()
            if ret:
                available.append(dev)
            cap.release()
    return available


# ============================================================
# 主客户端类
# ============================================================
class EmbeddedCameraClient:
    """
    i.MX6U 嵌入式摄像头推流客户端

    与 Windows 版的核心区别：
    - 使用 V4L2 后端，设备路径为字符串 "/dev/videoX"
    - 帧捕获在独立线程、帧发送在事件循环，二者用「最新帧 + Event」交接
    - 只保留最新一帧，发送跟不上时旧帧自然被覆盖，不会积压
    """

    def __init__(self, config: dict, device: str):
        self.server_url = config["server_url"]
        self.token = config["token"]
        self.device = device                             # 如 "/dev/video0"
        self.camera_name = config.get("camera_name", f"IMX6U-{device}")
        self.width = config.get("width", 640)
        self.height = config.get("height", 360)
        self.fps = config.get("fps", 15)
        self.quality = config.get("jpeg_quality", 50)

        self.running = False
        self.cap = None
        self.actual_resolution = f"{self.width}x{self.height}"

        # 跨线程交接：捕获线程只做整体赋值 + 置位 Event，事件循环负责读走
        self._loop: asyncio.AbstractEventLoop = None
        self._pending_frame = None
        self._frame_ready: asyncio.Event = None

        # 日志
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.StreamHandler(sys.stdout),
                # 同时写入日志文件，方便无显示器时排查问题
                logging.FileHandler("/tmp/camsee_client.log", encoding="utf-8"),
            ],
        )
        self.logger = logging.getLogger("CamSee-IMX6U")

    def open_camera(self):
        """使用 V4L2 后端打开摄像头"""
        self.logger.info(f"正在以 V4L2 后端打开摄像头 {self.device} ...")

        # 优先使用 V4L2，如果失败再用默认后端
        self.cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            self.logger.warning("V4L2 后端打开失败，尝试默认后端...")
            self.cap = cv2.VideoCapture(self.device)

        if not self.cap.isOpened():
            raise RuntimeError(f"无法打开摄像头 {self.device}，请检查 USB 连接和权限 (ls -l /dev/video*)")

        # 先设 MJPG 格式（如果摄像头支持），可大幅降低 USB 带宽占用。
        # 注意必须在设分辨率之前，否则部分 UVC 摄像头会重置分辨率
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))

        # 再设置分辨率和帧率
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self.cap.set(cv2.CAP_PROP_FPS, self.fps)

        actual_w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.actual_resolution = f"{actual_w}x{actual_h}"
        self.logger.info(
            f"摄像头已打开 | 实际分辨率: {actual_w}x{actual_h} | 实际帧率: {actual_fps}"
        )

    def _capture_frames_sync(self):
        """
        同步帧捕获循环（在独立线程中运行，避免阻塞 asyncio 事件循环）。

        只保留最新一帧：整体赋值在 CPython 里是原子的，无需加锁；
        Event 不是线程安全的，必须通过 call_soon_threadsafe 唤醒事件循环。
        发送跟不上时，旧帧会被新帧自然覆盖，不会积压。
        """
        frame_interval = 1.0 / self.fps
        while self.running:
            loop_start = time.time()

            ret, frame = self.cap.read()
            if not ret:
                self.logger.warning("读取帧失败，跳过...")
                time.sleep(0.05)
                continue

            # JPEG 编码
            encode_params = [cv2.IMWRITE_JPEG_QUALITY, self.quality]
            ok, buf = cv2.imencode(".jpg", frame, encode_params)
            if not ok:
                continue

            self._pending_frame = buf.tobytes()
            try:
                self._loop.call_soon_threadsafe(self._frame_ready.set)
            except RuntimeError:
                break          # 事件循环已关闭，退出线程

            # 控制采集帧率
            elapsed = time.time() - loop_start
            sleep_time = max(0, frame_interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

    async def _send_frames(self, ws):
        """
        异步帧发送循环：等待「有新帧」信号，取走最新帧并发送。
        """
        frame_count = 0
        report_interval = max(1, self.fps * 10)  # 每 10 秒报告一次

        while self.running:
            try:
                # 最多等 1 秒，避免连接异常时僵死
                await asyncio.wait_for(self._frame_ready.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            self._frame_ready.clear()
            jpeg_bytes = self._pending_frame
            if jpeg_bytes is None:
                continue

            try:
                await ws.send(jpeg_bytes)
            except Exception as e:
                self.logger.warning(f"发送帧时出现异常: {e}")
                break

            frame_count += 1
            if frame_count % report_interval == 0:
                self.logger.info(f"已发送 {frame_count} 帧（仅保留最新帧，无积压）")

    async def stream(self):
        """
        主推流循环，支持断线自动重连（指数退避）。

        采集线程与 WebSocket 连接解耦：只启动一次，重连期间持续采集，
        重连成功后立刻就能拿到最新一帧，不会因为反复拉起线程而残留多份。
        """
        self.running = True
        reconnect_delay = 1
        self._loop = asyncio.get_running_loop()
        self._frame_ready = asyncio.Event()

        capture_future = self._loop.run_in_executor(None, self._capture_frames_sync)

        try:
            while self.running:
                try:
                    ws_url = f"{self.server_url}/ws/camera"
                    self.logger.info(f"正在连接服务器 {ws_url} ...")

                    async with websockets.connect(
                        ws_url,
                        **ws_auth_kwargs(self.token),
                        max_size=10 * 1024 * 1024,
                        ping_interval=20,
                        ping_timeout=10,
                    ) as ws:
                        # 发送摄像头元信息（分辨率用实际值）
                        await ws.send(json.dumps({
                            "type": "camera_info",
                            "camera_name": self.camera_name,
                            "resolution": self.actual_resolution,
                            "fps": self.fps,
                            "device": self.device,
                            "platform": "Linux/ARM (i.MX6U)",
                        }))

                        self.logger.info("✅ 已连接，开始推流...")
                        reconnect_delay = 1

                        # 在当前协程中运行异步的帧发送，采集在后台线程
                        await self._send_frames(ws)

                except websockets.exceptions.InvalidHandshake as e:
                    self.logger.error(
                        f"握手被拒绝，通常是令牌不匹配，请核对 config.yaml 的 token: {e}"
                    )
                except websockets.exceptions.ConnectionClosed as e:
                    self.logger.warning(f"连接已断开: {e}")
                except ConnectionRefusedError:
                    self.logger.warning("连接被拒绝，服务器可能未启动")
                except OSError as e:
                    self.logger.warning(f"网络错误: {e}")
                except Exception as e:
                    self.logger.error(f"未知错误: {e}")

                if self.running:
                    self.logger.info(f"将在 {reconnect_delay} 秒后重连...")
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, 30)
        finally:
            # 停止采集线程。若 cap.read() 阻塞（例如摄像头被拔出），
            # 最多等 3 秒，避免整个进程永久卡在这里
            self.running = False
            try:
                await asyncio.wait_for(capture_future, timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self.logger.warning("采集线程未在 3 秒内退出（可能阻塞在读取帧），已放弃等待")

    def stop(self):
        """优雅停止"""
        self.running = False
        if self.cap and self.cap.isOpened():
            self.cap.release()
        self.logger.info("客户端已停止")


# ============================================================
# 入口
# ============================================================
async def main():
    args = parse_args()
    config = load_config(args.config)

    # ---- 摄像头设备选择 ----
    if args.list_cameras:
        print("正在扫描 /dev/video* 设备...")
        cameras = get_available_cameras()
        if cameras:
            print(f"找到 {len(cameras)} 个可用摄像头：")
            for c in cameras:
                print(f"  ✅ {c}")
        else:
            print("❌ 未检测到可用摄像头")
        return

    if args.device:
        # 用户通过命令行指定了设备
        device = args.device
        print(f"使用指定设备: {device}")
    else:
        # 自动扫描检测
        print("正在扫描可用摄像头设备（/dev/video*），请稍候...")
        cameras = get_available_cameras()

        if not cameras:
            print("❌ 未检测到任何可用摄像头！")
            print("   请检查：")
            print("   1. USB 摄像头是否插好")
            print("   2. 是否有读取权限：sudo chmod a+rw /dev/video*")
            sys.exit(1)

        print(f"\n✅ 检测到以下可用摄像头:")
        for i, cam in enumerate(cameras):
            label = "（首选/默认）" if i == 0 else ""
            print(f"  👉 {cam} {label}")

        if len(cameras) == 1:
            device = cameras[0]
            print(f"\n只有一个可用设备，自动使用 {device}")
        else:
            choice = input(f"\n请输入要使用的设备路径（直接回车使用 {cameras[0]}）: ").strip()
            device = choice if choice in cameras else cameras[0]

    # ---- 启动客户端 ----
    client = EmbeddedCameraClient(config, device)

    # 信号处理（兼容 Linux systemd 发送的 SIGTERM）
    def signal_handler(sig, frame):
        print(f"\n收到信号 {sig}，正在停止...")
        client.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        client.open_camera()
        await client.stream()
    finally:
        client.stop()


if __name__ == "__main__":
    asyncio.run(main())
