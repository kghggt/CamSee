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
    - 帧捕获与帧发送通过 asyncio.Queue 解耦，避免低功耗 CPU 积压
    - 队列满时丢弃旧帧而非阻塞，确保实时性
    """

    # 发送队列最大长度，超出则丢弃旧帧（防积压）
    QUEUE_MAX_SIZE = 3

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
        self._frame_queue: asyncio.Queue = None

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

        # 设置分辨率和帧率
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self.cap.set(cv2.CAP_PROP_FPS, self.fps)

        # 使用 MJPG 格式（如果摄像头支持），可大幅降低 USB 带宽占用
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))

        actual_w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.logger.info(
            f"摄像头已打开 | 实际分辨率: {actual_w}x{actual_h} | 实际帧率: {actual_fps}"
        )

    def _capture_frames_sync(self):
        """
        同步帧捕获循环（在独立线程中运行，避免阻塞 asyncio 事件循环）
        捕获到的帧放入队列；队列满时丢弃最旧的帧以保证实时性。
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

            jpeg_bytes = buf.tobytes()

            # 如果队列已满，丢弃最旧的帧（非阻塞）
            if self._frame_queue.qsize() >= self.QUEUE_MAX_SIZE:
                try:
                    self._frame_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass

            try:
                self._frame_queue.put_nowait(jpeg_bytes)
            except asyncio.QueueFull:
                pass  # 再次满了也无所谓，直接丢弃

            # 控制采集帧率
            elapsed = time.time() - loop_start
            sleep_time = max(0, frame_interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

    async def _send_frames(self, ws):
        """
        异步帧发送循环：从队列中取帧并通过 WebSocket 发送
        """
        frame_count = 0
        report_interval = self.fps * 10  # 每 10 秒报告一次

        while self.running:
            try:
                # 等待队列中有帧可取（最多等 1 秒，防止连接断开后僵死）
                jpeg_bytes = await asyncio.wait_for(
                    self._frame_queue.get(), timeout=1.0
                )
                await ws.send(jpeg_bytes)

                frame_count += 1
                if frame_count % report_interval == 0:
                    queue_size = self._frame_queue.qsize()
                    self.logger.info(
                        f"已发送 {frame_count} 帧 | 队列积压: {queue_size}/{self.QUEUE_MAX_SIZE}"
                    )

            except asyncio.TimeoutError:
                # 队列超时：正常现象，继续等
                continue
            except Exception as e:
                self.logger.warning(f"发送帧时出现异常: {e}")
                break

    async def stream(self):
        """
        主推流循环，支持断线自动重连（指数退避）
        捕获线程和发送协程并行运行
        """
        self.running = True
        reconnect_delay = 1

        while self.running:
            # 每次重连时重新创建一个干净的帧队列
            self._frame_queue = asyncio.Queue(maxsize=self.QUEUE_MAX_SIZE * 2)

            try:
                ws_url = f"{self.server_url}/ws/camera"
                self.logger.info(f"正在连接服务器 {ws_url} ...")

                async with websockets.connect(
                    ws_url,
                    additional_headers={"Authorization": f"Bearer {self.token}"},
                    max_size=10 * 1024 * 1024,
                    ping_interval=20,
                    ping_timeout=10,
                ) as ws:
                    # 发送摄像头元信息
                    await ws.send(json.dumps({
                        "type": "camera_info",
                        "camera_name": self.camera_name,
                        "resolution": f"{self.width}x{self.height}",
                        "fps": self.fps,
                        "device": self.device,
                        "platform": "Linux/ARM (i.MX6U)",
                    }))

                    self.logger.info("✅ 已连接，开始推流...")
                    reconnect_delay = 1

                    # 在独立线程中运行同步的帧捕获，不阻塞 asyncio
                    loop = asyncio.get_event_loop()
                    capture_task = loop.run_in_executor(None, self._capture_frames_sync)

                    # 在当前协程中运行异步的帧发送
                    try:
                        await self._send_frames(ws)
                    finally:
                        # 发送循环结束（连接断开），停止捕获线程
                        self.running = False
                        await capture_task
                        self.running = True  # 重连时重置

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
