@echo off
echo ============================================
echo   CamSee 部署脚本
echo   目标: ubuntu@<removed-host>:/data1/lm/CamSee
echo ============================================
echo.

echo [1/3] 创建远程目录...
ssh -p <removed-port> ubuntu@<removed-host> "mkdir -p /data1/lm/CamSee"

echo.
echo [2/3] 上传 server 文件夹...
scp -P <removed-port> -r d:\My_project\CamSee\server ubuntu@<removed-host>:/data1/lm/CamSee/

echo.
echo [3/3] 验证上传结果...
ssh -p <removed-port> ubuntu@<removed-host> "ls -la /data1/lm/CamSee/server/"

echo.
echo ============================================
echo   上传完成！
echo   下一步请 SSH 登录服务器安装依赖并启动:
echo     ssh -p <removed-port> ubuntu@<removed-host>
echo     cd /data1/lm/CamSee/server
echo     pip3 install -r requirements.txt
echo     python3 server.py
echo ============================================
pause
