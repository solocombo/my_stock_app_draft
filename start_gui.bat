@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 找 Python：優先用 py 啟動器，沒有就用 python
where py >nul 2>nul && (set PY=py) || (set PY=python)
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo [錯誤] 找不到 Python。請到 https://www.python.org/downloads/ 安裝，安裝時勾選 "Add python.exe to PATH"。
  pause
  exit /b 1
)

rem 第一次執行時自動安裝套件
%PY% -c "import streamlit, pandas, yaml" >nul 2>nul
if errorlevel 1 (
  echo 第一次執行，正在安裝套件，約需 1~3 分鐘...
  %PY% -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [錯誤] 套件安裝失敗，請把上面的訊息貼給 Claude。
    pause
    exit /b 1
  )
)

echo.
echo 電腦瀏覽器：http://localhost:8501
echo 手機（同一個 Wi-Fi）：http://（下面列出的 IPv4 位址）:8501
ipconfig | findstr /i "IPv4"
echo.
echo 關閉這個視窗就會停止服務。
%PY% -m streamlit run app.py --server.address 0.0.0.0
pause
