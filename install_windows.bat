@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo ============================================================
echo   SubVideo - cai dat tren Windows
echo ============================================================
echo.

where py >nul 2>nul
if errorlevel 1 (
  echo [LOI] Chua cai Python. Hay cai Python 3.11 tu https://www.python.org/downloads/
  echo       ^(nho tick "Add python.exe to PATH" va "py launcher"^)
  pause
  exit /b 1
)

if not exist .venv (
  echo [1/6] Tao moi truong ao Python 3.11 ...
  py -3.11 -m venv .venv || py -3 -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip wheel setuptools

echo [2/6] Cai thu vien loi (giao dien, OCR, tai video, dich API) ...
pip install -r requirements.txt || goto :fail

echo [3/6] Cai PyTorch ban CUDA cho card NVIDIA ...
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128 || goto :fail

echo [4/6] Cai cac mo hinh AI (tach giong, nhan dang, long tieng) ...
pip install -r requirements-ai.txt
if errorlevel 1 (
  echo [CANH BAO] Mot so thu vien AI cai khong thanh cong. Xem tab Cai dat ^> Thu vien AI trong ung dung.
)

echo [5/6] Dung onnxruntime ban GPU ...
pip uninstall -y onnxruntime onnxruntime-gpu >nul 2>nul
pip install onnxruntime-gpu

echo [6/6] Tai FFmpeg va trinh phat mpv vao thu muc bin\ ...
python scripts\setup_tools.py

echo.
echo ============================================================
echo   Xong! Chay run.bat de mo SubVideo.
echo   Neu muon dung Qwen3-ASR: chay install_qwen_env.bat
echo ============================================================
pause
exit /b 0

:fail
echo.
echo [LOI] Cai dat that bai. Hay chup man hinh loi o tren de duoc ho tro.
pause
exit /b 1
