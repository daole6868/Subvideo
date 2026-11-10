@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo Tao moi truong rieng cho Qwen3-ASR (can transformers 4.57) ...
if not exist .venv-qwen (
  py -3.11 -m venv .venv-qwen || py -3 -m venv .venv-qwen
)
call .venv-qwen\Scripts\activate.bat
python -m pip install --upgrade pip wheel
pip install numpy scipy soundfile av opencv-python pypinyin rapidfuzz opencc || goto :fail
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128 || goto :fail
pip install qwen-asr silero-vad funasr modelscope || goto :fail
echo.
echo Xong. Trong SubVideo: Cai dat ^> Duong dan ^> "Python cho Qwen3-ASR" chon:
echo    %~dp0.venv-qwen\Scripts\python.exe
pause
exit /b 0
:fail
echo [LOI] Cai dat that bai.
pause
exit /b 1
