"""
CamSee - 摄像头采集客户端

从 USB 摄像头采集视频帧，通过 WebSocket 推送到云服务器。
支持断线自动重连、帧率控制、JPEG 压缩。
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
from pathlib import Path

# 配置文件路径
CONFIG_PATH = Path(__file__).parent / "config.yaml"


def load_config():
    """加载配置文件"""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class CameraClient:
    """USB 摄像头采集和推流客户端"""

    def __init__(self, config: dict):
        self.server_url = config["server_url"]
        self.token = config["token"]
        self.camera_id = config.get("camera_id", 0)
        self.camera_name = config.get("camera_name", "Camera-1")
        self.width = config.get("width", 1280)
        self.height = config.get("height", 720)
        self.fps = config.get("fps", 15)
        self.quality = config.get("jpeg_quality", 70)

        self.running = False
        self.cap = None

        # 日志
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
        )
        self.logger = logging.getLogger("CamSee-Client")

    def open_camera(self):
        """打开 USB 摄像头"""
        self.logger.info(f"正在打开摄像头 {self.camera_id} ...")
        self.cap = cv2.VideoCapture(self.camera_id)

        if not self.cap.isOpened():
            raise RuntimeError(f"无法打开摄像头 {self.camera_id}，请检查连接")

        # 设置分辨率
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)

        actual_w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.logger.info(f"摄像头已打开，实际分辨率: {actual_w}x{actual_h}")

    async def stream(self):
        """主推流循环，支持断线自动重连"""
        self.running = True
        reconnect_delay = 1

        while self.running:
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
                    # 发送摄像头信息
                    await ws.send(
                        json.dumps(
                            {
                                "type": "camera_info",
                                "camera_name": self.camera_name,
                                "resolution": f"{self.width}x{self.height}",
                                "fps": self.fps,
                            }
                        )
                    )

                    self.logger.info("✅ 已连接，开始推流...")
                    reconnect_delay = 1
                    frame_interval = 1.0 / self.fps
                    frame_count = 0

                    while self.running:
                        start_time = time.time()

                        ret, frame = self.cap.read()
                        if not ret:
                            self.logger.warning("读取帧失败，重试...")
                            await asyncio.sleep(0.1)
                            continue

                        # JPEG 编码
                        encode_params = [cv2.IMWRITE_JPEG_QUALITY, self.quality]
                        _, buffer = cv2.imencode(".jpg", frame, encode_params)

                        # 发送二进制帧
                        await ws.send(buffer.tobytes())

                        frame_count += 1
                        if frame_count % (self.fps * 10) == 0:
                            self.logger.info(f"已发送 {frame_count} 帧")

                        # 帧率控制
                        elapsed = time.time() - start_time
                        sleep_time = max(0, frame_interval - elapsed)
                        if sleep_time > 0:
                            await asyncio.sleep(sleep_time)

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
        """停止客户端"""
        self.running = False
        if self.cap and self.cap.isOpened():
            self.cap.release()
        self.logger.info("客户端已停止")


def get_available_cameras():
    """扫描可用的摄像头设备"""
    available_cameras = []
    print("正在扫描可用摄像头设备，请稍候...")
    # 扫描 0-4 号摄像头
    for i in range(5):
        # 尝试使用 DirectShow (在 Windows 上打开速度更快)
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(i)
            
        if cap.isOpened():
            ret, _ = cap.read()
            if ret:
                available_cameras.append(i)
            cap.release()
    return available_cameras

async def main():
    config = load_config()
    
    # ==========================================
    # 摄像头选择逻辑
    # ==========================================
    cameras = get_available_cameras()
    if not cameras:
        print("\n❌ 未检测到任何可用摄像头！请检查 USB 连接或设备权限。")
        sys.exit(1)
        
    print("\n✅ 检测到以下可用摄像头:")
    for cam_id in cameras:
        name = "笔记本内置摄像头" if cam_id == 0 else f"外接 USB 摄像头 {cam_id}"
        print(f"  👉 [{cam_id}] {name}")
        
    if len(cameras) == 1:
        selected_id = cameras[0]
        print(f"\n只有一个可用设备，将自动使用摄像头 [{selected_id}]")
    else:
        default_id = config.get("camera_id", 0)
        if default_id not in cameras:
            default_id = cameras[0]
            
        print(f"\n❓ 请输入要使用的摄像头编号")
        choice = input(f"   (直接按回车键默认使用 [{default_id}]): ").strip()
        
        if choice.isdigit() and int(choice) in cameras:
            selected_id = int(choice)
        else:
            selected_id = default_id
            
    config["camera_id"] = selected_id
    if selected_id != 0:
        config["camera_name"] = f"外接摄像头-{selected_id}"
    # ==========================================

    client = CameraClient(config)

    # 处理 Ctrl+C
    def signal_handler(sig, frame):
        print("\n正在停止...")
        client.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    try:
        client.open_camera()
        await client.stream()
    finally:
        client.stop()

if __name__ == "__main__":
    asyncio.run(main())
