@echo off
REM ============================================
REM  部署目标配置（改成你自己的，不要提交真实地址）
REM ============================================
set SSH_PORT=你的SSH端口
set SSH_USER=你的用户名
set SERVER_IP=你的服务器IP
set REMOTE_DIR=/opt/camsee

echo ============================================
echo   CamSee 部署脚本
echo   目标: %SSH_USER%@%SERVER_IP%:%REMOTE_DIR%
echo ============================================
echo.

echo [1/3] 创建远程目录...
ssh -p %SSH_PORT% %SSH_USER%@%SERVER_IP% "mkdir -p %REMOTE_DIR%"

echo.
echo [2/3] 上传 server 文件夹...
scp -P %SSH_PORT% -r "%~dp0server" %SSH_USER%@%SERVER_IP%:%REMOTE_DIR%/

echo.
echo [3/3] 验证上传结果...
ssh -p %SSH_PORT% %SSH_USER%@%SERVER_IP% "ls -la %REMOTE_DIR%/server/"

echo.
echo ============================================
echo   上传完成！
echo   下一步请 SSH 登录服务器安装依赖并启动:
echo     ssh -p %SSH_PORT% %SSH_USER%@%SERVER_IP%
echo     cd %REMOTE_DIR%/server
echo     pip3 install -r requirements.txt
echo     export CAMSEE_TOKEN='你的令牌'
echo     python3 server.py
echo ============================================
pause
