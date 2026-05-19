# Auto Clicker - Image Based - macOS

Tool auto-click theo image template trên macOS, **không chiếm chuột thật của user**.
Tool gửi mouse event thẳng vào process target qua `CGEventPostToPid` nên cursor của
bạn vẫn rảnh để dùng việc khác trong khi tool tự click vào window đã chọn.

## Tính năng

- **Window picker**: liệt kê tất cả cửa sổ đang mở (cả off-screen), filter theo tên app/title
- **Live preview** cửa sổ được chọn (refresh 0.5s), capture qua `CGWindowListCreateImage`
- **Region selector**: kéo chuột để cắt template ngay từ screenshot
- **Multi-scale + grayscale matching** với OpenCV `matchTemplate` (TM_CCOEFF_NORMED)
- **Non-intrusive click**: post event qua PID, không di chuyển cursor user
- **Cấu hình job phong phú**: threshold, click type (left/right/middle/double), interval + jitter, click offset, click jitter px, max clicks, stop-after-N-misses, multi-scale toggle, grayscale toggle
- **Hotkey global**: `Cmd+Shift+S` start/stop, `Cmd+Shift+P` pause/resume
- **Log + thống kê realtime** trong UI
- **Test match button** để xác minh template trước khi chạy

## Yêu cầu

- macOS (Apple Silicon hoặc Intel)
- Python 3.9+
- Cấp 2 quyền cho terminal/Python:
  - **Screen Recording** (System Settings → Privacy & Security → Screen Recording)
  - **Accessibility** (System Settings → Privacy & Security → Accessibility)

> Lưu ý: nếu chạy trong Terminal/iTerm, app cần cấp quyền cho **Terminal**, không phải
> cho Python. Sau khi tick xong, **quit và mở lại** terminal để có hiệu lực.

## Cài đặt

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Chạy

```bash
PYTHONPATH=. python run.py
# hoặc
PYTHONPATH=. python -m auto_clicker.main
```

## Quy trình dùng

1. Cấp quyền (menu **Permissions → Kiểm tra quyền**)
2. Chọn cửa sổ ở danh sách bên trái → preview xuất hiện ở giữa
3. Bấm **Cắt template từ window**, kéo chuột chọn vùng nhận dạng
   (ví dụ icon nút cần click) → template được lưu trong `auto_clicker/assets/`
4. Tinh chỉnh threshold (0.85 mặc định), click type, interval...
5. Bấm **Test match ngay** để kiểm tra xem template có tìm được không (sẽ vẽ overlay xanh)
6. Bấm **Start** để chạy. Cursor user vẫn tự do di chuyển/click chỗ khác
7. Bấm **Stop** hoặc dùng `Cmd+Shift+S` để dừng

## Smoke test

```bash
PYTHONPATH=. python scripts/smoke_test.py
PYTHONPATH=. python scripts/gui_test.py   # chạy ở offscreen mode
```

## Cấu trúc

```
auto_clicker/
├── main.py                  Entry point
├── core/
│   ├── window_manager.py    Liệt kê + capture window qua Quartz
│   ├── click_engine.py      Non-intrusive click (CGEventPostToPid)
│   ├── image_matcher.py     OpenCV template matching
│   └── automation.py        Job runner threaded
├── ui/
│   ├── main_window.py       Main UI PySide6
│   └── region_selector.py   Crop dialog
├── utils/
│   ├── permissions.py       Check Screen Recording + Accessibility
│   ├── hotkey.py            Global hotkey via pynput
│   └── qt_utils.py          ndarray ↔ QPixmap
└── assets/                  Templates được lưu ở đây
```

## Mẹo sử dụng

- **App game/app full-screen Metal**: `CGEventPostToPid` có thể không có tác dụng với
  một số game dùng IOKit HID trực tiếp. Trường hợp đó thử để `pid=0` (chiếm chuột thật)
  bằng cách sửa `JobConfig.pid = 0`.
- **Retina display**: capture trả pixel resolution gấp 2x point. Code tự map từ pixel
  về point qua `(local_x_pt = cx_px / sw_px * win.width)` nên không cần lo.
- **Window di chuyển khi đang chạy**: tool refresh window info mỗi vòng lặp nên click
  vẫn đúng vị trí mới của window.
- **Confidence quá cao mà vẫn miss**: thử bật multi-scale, hoặc tắt grayscale nếu
  template phụ thuộc nhiều vào màu.
- **App target không nhận click**: thử click vào tọa độ khác (offset), hoặc activate
  app trước thủ công 1 lần. Một số app cần focus mới nhận event.

## Hạn chế đã biết

- Mouse event không di chuyển con trỏ thật (đó là feature). Hệ quả là một số animation
  hover trong target app sẽ không xảy ra.
- Một số game/app dùng raw HID input (Metal/IOKit) không nhận `CGEventPostToPid`.
- Capture được window kể cả background, nhưng macOS sẽ refuse capture window thuộc
  process khác user/sandbox.
