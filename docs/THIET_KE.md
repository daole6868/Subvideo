# Thiết kế SubVideo

## Luồng dữ liệu
```
yt-dlp / file ─► probe (PTS, VFR) ─► audio/original.wav (48 kHz)
   ├─► Tách nguồn (RoFormer) ─► vocals.wav / instrumental.wav (+ vocals_dry.wav) ─► asr16k.wav
   │       └─► VAD ─► ASR (mốc từng chữ) ─► người nói (CAM++/pyannote) ─► asr/asr.json
   ├─► OCR vùng chọn (mọi khung hình, mặt nạ chữ + so khung) ─► ocr/ocr.json (+ ảnh crop)
   └─► Cắt cảnh
         ▼
   HỢP NHẤT: căn chữ OCR với chữ ASR (Needleman–Wunsch, đồng âm = tin OCR) ─► tinh chỉnh mép theo năng lượng
         ▼                                                     ─► project.json: segments[]
   Hồ sơ phim (nhân vật, xưng hô, thuật ngữ, cảnh) ─► Dịch theo cảnh: vi_sub + vi_dub (≤ max_syllables) ─► Soát
         ▼
   TTS theo nhân vật/cảm xúc ─► tts/raw ─► Bộ căn nhịp ─► tts/final + vùng làm chậm video (time map)
         ▼
   Bộ trộn (dùng chung xem trước/xuất) ─► render/mix.wav + render/subs_vi.ass ─► FFmpeg xuất
```

## Nguyên tắc
* `project.json` là nguồn sự thật duy nhất; thời gian lưu bằng **ms nguyên trên trục thời gian gốc**.
  Chỉ khi dựng mới đổi sang trục thời gian đầu ra qua `TimeMap` (khi có đoạn làm chậm video).
* Mỗi câu có 2 loại thời gian: **thời gian nói** (start/end — để đặt lồng tiếng) và **thời gian hiển thị**
  (disp_start/disp_end — bám khung hình, quy tắc đọc, cắt cảnh).
* Mỗi bước là một hàm `stage(project, params, ctx)` chạy trong **tiến trình nền** (worker) — mô hình được giữ
  trong bộ nhớ giữa các lần chạy, lỗi/crash không làm sập giao diện, có thể dùng Python riêng cho engine xung đột.
* Tab Tự động gọi lần lượt các stage và **không bao giờ** gọi `export`.

## Căn nhịp (giãn tiếng hay giãn video?)
Thứ tự ưu tiên, từ ít ảnh hưởng nhất:
1. Ngân sách âm tiết khi dịch: `max_syllables = (thời lượng câu + khoảng mượn được) × nhịp mục tiêu của nhân vật`.
2. TTS sinh đúng tốc độ / đúng thời lượng (OmniVoice `duration`).
3. Mượn khoảng lặng trước (≤ 200 ms) và sau câu (≤ 500 ms, tới sát câu kế).
4. Co giãn giọng giữ cao độ (Rubber Band R3), tối đa ×1.20 (bắt buộc ×1.35).
5. Làm chậm video cục bộ (≥ 0.85×), nhạc nền cùng đoạn được giãn theo.
6. Đánh dấu `need_shorten` → rút gọn lời dịch → lồng lại (tab Tự động làm vòng này tự động).
Câu ngắn hơn chỗ trống **không** bị kéo chậm (tránh giọng lè nhè) — giữ tốc độ tự nhiên, bắt đầu khi nhân vật mở miệng.
