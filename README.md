# SubVideo — Dịch & lồng tiếng video Trung → Việt

Ứng dụng Windows (Python + PySide6) gồm đủ các bước: **tải video → tách giọng/nhạc nền → nghe & nhận dạng lời →
quét phụ đề cứng (OCR) → hợp nhất hình + tiếng → hồ sơ nhân vật & xưng hô → dịch theo ngữ cảnh → lồng tiếng →
căn nhịp → trộn âm → xuất video chất lượng cao**. Mỗi bước là một tab. Ngoài ra có tab **Tự động** (chạy hết
rồi dừng trước khi xuất) và tab **Chỉnh sửa & căn chỉnh** (không xuất).

---

## 1. Cài đặt (Windows 10/11, card NVIDIA)

1. Cài **Python 3.11** từ python.org (tick *Add python.exe to PATH*). Cập nhật driver NVIDIA mới nhất.
2. Tải mã nguồn về (Code → Download ZIP) và giải nén, ví dụ vào `D:\SubVideo`.
3. Chạy **`install_windows.bat`**. Script sẽ:
   * tạo môi trường `.venv`, cài thư viện lõi,
   * cài PyTorch bản CUDA và các mô hình AI (tách giọng, FunASR, OmniVoice, VieNeu…),
   * tải **FFmpeg** và **libmpv** vào `bin\`.
4. Mở ứng dụng bằng **`run.bat`**.
5. Vào **⚙️ Cài đặt**: nhập khoá API (Gemini / Claude / OpenAI / DeepSeek…) hoặc cấu hình Ollama để dịch bằng máy.

Tuỳ chọn:
* **Qwen3-ASR** (nhận dạng chính xác hơn, 22 phương ngữ) xung đột thư viện với OmniVoice → chạy
  `install_qwen_env.bat` rồi chọn `.venv-qwen\Scripts\python.exe` ở *Cài đặt → Python cho Qwen3-ASR*.
* **Rubber Band** (`rubberband.exe` vào `bin\`) cho co giãn giọng chất lượng cao nhất (engine R3).
* **Từ điển Hán-Việt** (Names.txt, ChinesePhienAmWords.txt định dạng QuickTranslator) để phiên âm tên riêng
  nhất quán: chọn thư mục ở *Cài đặt → Từ điển Hán-Việt*.

Mô hình AI được tải về ở lần dùng đầu tiên (vài GB). Có thể đổi nơi lưu ở *Cài đặt → Thư mục mô hình*.

## 2. Quy trình làm một tập phim

| Tab | Việc làm | Ghi chú |
|---|---|---|
| 1. Dự án & tải video | Chọn file hoặc dán link (YouTube, Bilibili, TikTok, Douyin…) | Có thể tải kèm phụ đề có sẵn |
| 2. Tách giọng | BS-RoFormer / Mel-RoFormer tách lời thoại khỏi nhạc nền + tiếng động | Tuỳ chọn khử vang cho nhận dạng |
| 3. Nghe | VAD + nhận dạng tiếng Trung có mốc thời gian **từng chữ** + phân biệt người nói (+ cảm xúc) | FunASR / Qwen3-ASR / Whisper |
| 4. OCR | Kéo chọn vùng phụ đề; quét **từng khung hình** để biết đúng khung phụ đề hiện/tắt | Nút *Thử tại khung này* để chỉnh màu chữ |
| 5. Hợp nhất | Ghép chữ trên hình (đúng chữ) với chữ nghe được (đúng thời điểm), tinh chỉnh mép câu theo năng lượng giọng | Câu lệch được đánh dấu |
| 6. Nhân vật | AI đề xuất nhân vật, **bảng xưng hô**, thuật ngữ; gán giọng cho từng nhân vật | Nhập/xuất hồ sơ giữa các tập |
| 7. Dịch | Dịch theo cảnh, có 2 bản: **phụ đề** và **lời lồng tiếng** (giới hạn số âm tiết) | Soát lỗi xưng hô bằng AI |
| 8. Lồng tiếng | OmniVoice (nhái giọng, đặt thời lượng chính xác) / VieNeu-TTS / Edge TTS | Chỉ tạo lại câu đã sửa |
| 9. Căn nhịp | Mượn khoảng lặng → tạo lại đúng thời lượng → co giãn giọng → làm chậm video cục bộ | Câu quá dài được đánh dấu rút gọn |
| 10. Trộn âm | 3 thanh âm lượng: **giọng lồng / giọng gốc / nhạc nền**, ducking, chuẩn độ to LUFS | Phụ đề / Thuyết minh / Lồng tiếng |
| 11. Xuất | x264/x265/NVENC/AV1, in phụ đề hoặc phụ đề rời, làm mờ phụ đề gốc, thêm track gốc | Xem trước chính xác 20 giây |

**⚡ Tự động**: chọn các bước, bấm chạy — ứng dụng chạy liên tục tới *Trộn âm* rồi **dừng, không bao giờ tự xuất**.
Có hàng đợi để chạy nhiều tập liên tiếp.

**✏️ Chỉnh sửa & căn chỉnh**: video + sóng âm + 3 làn (Thoại / Phụ đề / Lồng tiếng). Kéo mép câu (phóng tới
1 ms/điểm ảnh bằng Ctrl + lăn chuột), sửa lời, nghe giọng gốc / giọng lồng, dịch lại / rút gọn / tạo lại giọng
từng câu, tách / gộp câu. Phím tắt: `Ctrl+Space` phát/dừng, `Ctrl+Enter` phát câu, `Ctrl+↑/↓` câu trước/sau,
`Alt+←/→` từng khung, `Ctrl+[` / `Ctrl+]` đặt đầu/cuối câu tại vị trí phát, `Ctrl+R` đánh dấu đã duyệt.

### "Xuất ra y hệt"
Âm thanh xem trước và âm thanh xuất là **cùng một file** `render/mix.wav` do cùng bộ trộn tạo ra; phụ đề là cùng
file `.ass` được libass vẽ (trình phát mpv và FFmpeg đều dùng libass). Khi có đoạn **làm chậm video**, trình chỉnh
sửa phát theo thời gian gốc; bấm **👁 Bản xuất 20s** để dựng đúng chuỗi xử lý của bản xuất.

## 3. Độ chính xác — nên hiểu đúng
* **Âm thanh**: mốc lời nói chính xác khoảng 10–20 ms (căn chữ + tinh chỉnh theo năng lượng giọng).
* **Phụ đề hiển thị**: theo khung hình (25 fps = 40 ms/khung) — "tới mili giây" nghĩa là đúng khung.
* **Không hệ thống tự động nào đúng 100%**: SubVideo tự làm phần lớn và **đánh dấu** những câu không chắc
  (chữ hình ≠ chữ nghe, không nghe thấy lời, câu lồng quá dài…) để bạn duyệt nhanh ở tab Chỉnh sửa.

## 4. Chạy không cần giao diện (CLI)
```bat
.venv\Scripts\python -m subvideo.cli new D:\Phim\Tap01 --video D:\Phim\tap01.mp4
.venv\Scripts\python -m subvideo.cli auto D:\Phim\Tap01 --skip ocr
.venv\Scripts\python -m subvideo.cli run D:\Phim\Tap01 export
.venv\Scripts\python -m subvideo.cli stages
```

## 5. Cấu trúc mã nguồn
```
subvideo/
  core/        mô hình dự án (project.json), thời gian, ffmpeg, âm thanh, phụ đề SRT/ASS, văn bản Việt/Trung
  pipeline/    download, separate, transcribe, ocr, fusion, scenes, translate (+llm, hanviet), tts,
               timing (bộ căn nhịp), stretch, mixer, export, stages (danh sách bước), timemap
  ui/          main_window, jobs (tiến trình nền), pages/ (mỗi bước một tab), widgets/ (timeline, player…)
  worker.py    tiến trình xử lý nền (giữ mô hình trong bộ nhớ, không làm treo giao diện)
tests/         kiểm thử đơn vị + chạy trọn quy trình trên video tổng hợp
```
Kiểm thử: `python -m pytest -q tests`.

## 6. Lưu ý pháp lý
* Kiểm tra giấy phép từng mô hình nếu dùng **thương mại** (một số mô hình/biến thể chỉ cho phi thương mại;
  pyannote community-1: CC-BY-4.0; ctc-forced-aligner: CC-BY-NC). OmniVoice, audio-separator, FunASR, RapidOCR:
  giấy phép mở — vẫn nên đọc lại trước khi bán phần mềm.
* Bản quyền phim gốc và việc nhái giọng diễn viên khi đăng công khai thuộc trách nhiệm người dùng.
  SubVideo không hỗ trợ tải nội dung có DRM.
