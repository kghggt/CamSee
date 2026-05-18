/**
 * CamSee 监控面板 - 前端逻辑
 *
 * 功能:
 * - WebSocket 连接管理（自动重连）
 * - 视频帧接收与 Canvas 渲染
 * - 状态信息轮询与展示
 * - 全屏切换、截图下载
 * - 系统日志显示
 */

(function () {
    'use strict';

    // ============================================
    // DOM 元素
    // ============================================
    const $ = (id) => document.getElementById(id);

    const els = {
        connectionBadge: $('connectionBadge'),
        timeDisplay: $('timeDisplay'),
        videoCanvas: $('videoCanvas'),
        videoOffline: $('videoOffline'),
        videoOverlay: $('videoOverlay'),
        videoContainer: $('videoContainer'),
        cameraLabel: $('cameraLabel'),
        fpsCounter: $('fpsCounter'),
        resolutionLabel: $('resolutionLabel'),
        fullscreenBtn: $('fullscreenBtn'),
        screenshotBtn: $('screenshotBtn'),
        reconnectBtn: $('reconnectBtn'),
        statCamera: $('statCamera'),
        statServer: $('statServer'),
        statLatency: $('statLatency'),
        statViewers: $('statViewers'),
        statName: $('statName'),
        statResolution: $('statResolution'),
        statFps: $('statFps'),
        statFrames: $('statFrames'),
        logContainer: $('logContainer'),
    };

    // ============================================
    // 状态
    // ============================================
    const state = {
        ws: null,
        connected: false,
        cameraOnline: false,
        frameCount: 0,
        fpsFrames: 0,
        lastFpsTime: Date.now(),
        currentFps: 0,
        reconnectDelay: 1000,
        reconnectTimer: null,
        statusTimer: null,
        cameraName: '主摄像头',
    };

    const ctx = els.videoCanvas.getContext('2d');

    // ============================================
    // 工具函数
    // ============================================
    function addLog(message, level = 'info') {
        const entry = document.createElement('div');
        entry.className = `log-entry log-${level}`;
        const now = new Date().toLocaleTimeString('zh-CN', { hour12: false });
        entry.textContent = `[${now}] ${message}`;
        els.logContainer.appendChild(entry);

        // 最多保留 100 条日志
        while (els.logContainer.children.length > 100) {
            els.logContainer.removeChild(els.logContainer.firstChild);
        }

        // 自动滚动到底部
        els.logContainer.scrollTop = els.logContainer.scrollHeight;
    }

    function updateTime() {
        const now = new Date();
        els.timeDisplay.textContent = now.toLocaleTimeString('zh-CN', {
            hour12: false,
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
        });
    }

    function setConnectionStatus(online) {
        state.connected = online;
        if (online) {
            els.connectionBadge.classList.add('online');
            els.connectionBadge.querySelector('.status-text').textContent = '已连接';
            els.statServer.textContent = '在线';
            els.statServer.className = 'stat-value online';
        } else {
            els.connectionBadge.classList.remove('online');
            els.connectionBadge.querySelector('.status-text').textContent = '未连接';
            els.statServer.textContent = '离线';
            els.statServer.className = 'stat-value offline';
        }
    }

    function setCameraStatus(online) {
        state.cameraOnline = online;
        if (online) {
            els.videoOffline.style.display = 'none';
            els.videoCanvas.style.display = 'block';
            els.videoOverlay.style.display = 'flex';
            els.statCamera.textContent = '在线';
            els.statCamera.className = 'stat-value online';
        } else {
            els.videoOffline.style.display = 'flex';
            els.videoCanvas.style.display = 'none';
            els.videoOverlay.style.display = 'none';
            els.statCamera.textContent = '离线';
            els.statCamera.className = 'stat-value offline';
        }
    }

    // ============================================
    // WebSocket 连接
    // ============================================
    function connectViewer() {
        if (state.ws) {
            try { state.ws.close(); } catch (e) { /* ignore */ }
        }

        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${location.host}/ws/viewer?camera=${encodeURIComponent(state.cameraName)}`;

        addLog(`正在连接服务器...`, 'info');

        const ws = new WebSocket(wsUrl);
        ws.binaryType = 'arraybuffer';

        ws.onopen = () => {
            state.ws = ws;
            state.reconnectDelay = 1000;
            setConnectionStatus(true);
            addLog('已连接到服务器', 'success');
        };

        ws.onmessage = (event) => {
            if (event.data instanceof ArrayBuffer) {
                renderFrame(event.data);
            }
        };

        ws.onclose = (event) => {
            setConnectionStatus(false);
            setCameraStatus(false);
            addLog(`连接断开 (code: ${event.code})`, 'warning');
            scheduleReconnect();
        };

        ws.onerror = () => {
            addLog('连接错误', 'error');
        };
    }

    function scheduleReconnect() {
        if (state.reconnectTimer) {
            clearTimeout(state.reconnectTimer);
        }

        addLog(`${state.reconnectDelay / 1000}秒后重连...`, 'info');

        state.reconnectTimer = setTimeout(() => {
            connectViewer();
        }, state.reconnectDelay);

        state.reconnectDelay = Math.min(state.reconnectDelay * 2, 30000);
    }

    // ============================================
    // 帧渲染
    // ============================================
    function renderFrame(arrayBuffer) {
        const blob = new Blob([arrayBuffer], { type: 'image/jpeg' });
        const url = URL.createObjectURL(blob);
        const img = new Image();

        img.onload = () => {
            // 首次收到帧时更新状态
            if (!state.cameraOnline) {
                setCameraStatus(true);
                addLog('摄像头画面已接入', 'success');
            }

            // 更新 Canvas 尺寸（仅在分辨率变化时）
            if (els.videoCanvas.width !== img.width || els.videoCanvas.height !== img.height) {
                els.videoCanvas.width = img.width;
                els.videoCanvas.height = img.height;
                els.resolutionLabel.textContent = `${img.width}×${img.height}`;
            }

            ctx.drawImage(img, 0, 0);
            URL.revokeObjectURL(url);

            // 帧计数与 FPS 计算
            state.frameCount++;
            state.fpsFrames++;
            els.statFrames.textContent = state.frameCount.toLocaleString();

            const now = Date.now();
            const elapsed = now - state.lastFpsTime;
            if (elapsed >= 1000) {
                state.currentFps = Math.round((state.fpsFrames * 1000) / elapsed);
                els.fpsCounter.textContent = `${state.currentFps} FPS`;
                state.fpsFrames = 0;
                state.lastFpsTime = now;
            }
        };

        img.onerror = () => {
            URL.revokeObjectURL(url);
        };

        img.src = url;
    }

    // ============================================
    // 状态轮询
    // ============================================
    async function fetchStatus() {
        try {
            const response = await fetch('/api/status');
            const data = await response.json();

            if (data.cameras && data.cameras.length > 0) {
                const cam = data.cameras[0];
                els.statName.textContent = cam.name || '--';
                els.statResolution.textContent = cam.resolution || '--';
                els.statFps.textContent = cam.fps ? `${cam.fps} fps` : '--';
                els.statViewers.textContent = cam.viewers || '0';
                els.cameraLabel.textContent = cam.name || '主摄像头';

                // 估算延迟（简单的 RTT / 2）
                const start = Date.now();
                await fetch('/api/status');
                const latency = Math.round((Date.now() - start) / 2);
                els.statLatency.textContent = `${latency} ms`;
            } else {
                els.statName.textContent = '--';
                els.statViewers.textContent = '0';
            }
        } catch (e) {
            // 静默处理
        }
    }

    // ============================================
    // 控制按钮
    // ============================================

    // 全屏
    els.fullscreenBtn.addEventListener('click', () => {
        if (document.fullscreenElement) {
            document.exitFullscreen();
            document.body.classList.remove('fullscreen-mode');
        } else {
            document.documentElement.requestFullscreen();
            document.body.classList.add('fullscreen-mode');
        }
    });

    document.addEventListener('fullscreenchange', () => {
        if (!document.fullscreenElement) {
            document.body.classList.remove('fullscreen-mode');
        }
    });

    // 截图
    els.screenshotBtn.addEventListener('click', () => {
        if (!state.cameraOnline) {
            addLog('摄像头未连接，无法截图', 'warning');
            return;
        }

        // 闪光效果
        const flash = document.createElement('div');
        flash.className = 'screenshot-flash';
        els.videoContainer.appendChild(flash);
        setTimeout(() => flash.remove(), 500);

        // 下载截图
        const link = document.createElement('a');
        const timestamp = new Date()
            .toLocaleString('zh-CN', { hour12: false })
            .replace(/[\/\s:]/g, '-');
        link.download = `CamSee_${timestamp}.jpg`;
        link.href = els.videoCanvas.toDataURL('image/jpeg', 0.95);
        link.click();

        addLog('截图已保存', 'success');
    });

    // 重连
    els.reconnectBtn.addEventListener('click', () => {
        if (state.reconnectTimer) {
            clearTimeout(state.reconnectTimer);
        }
        state.reconnectDelay = 1000;
        addLog('手动重新连接...', 'info');
        connectViewer();
    });

    // 键盘快捷键
    document.addEventListener('keydown', (e) => {
        if (e.key === 'f' || e.key === 'F') {
            els.fullscreenBtn.click();
        }
        if (e.key === 's' || e.key === 'S') {
            if (!e.ctrlKey) els.screenshotBtn.click();
        }
    });

    // ============================================
    // 初始化
    // ============================================
    function init() {
        addLog('CamSee 监控面板已加载', 'success');

        // 时钟
        updateTime();
        setInterval(updateTime, 1000);

        // 连接 WebSocket
        connectViewer();

        // 状态轮询（每 3 秒）
        fetchStatus();
        state.statusTimer = setInterval(fetchStatus, 3000);
    }

    // 页面加载完成后初始化
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
