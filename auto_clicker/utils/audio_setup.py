"""Helpers cho việc setup audio capture trên macOS.

Vấn đề: macOS không cho app capture audio output trực tiếp. Phải dùng virtual
audio driver (BlackHole / Loopback / Soundflower) để route output → input ảo.

Module này cung cấp:
- Detect các loopback driver đã cài.
- Generate hướng dẫn cài đặt + Multi-Output setup.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional


LOOPBACK_KEYWORDS = {
    "BlackHole": "BlackHole",
    "Loopback": "Rogue Amoeba Loopback",
    "Soundflower": "Soundflower",
    "VB-Cable": "VB-Cable",
    "iShowU": "iShowU Audio Capture",
    "Virtual": "Virtual Audio (sinh chung)",
}


@dataclass
class LoopbackInfo:
    name: str  # tên hiển thị (vd "BlackHole 2ch")
    vendor: str  # hãng
    device_index: int  # index trong sounddevice list


def detect_loopback_devices() -> list[LoopbackInfo]:
    """Tìm các loopback driver đã cài qua tên device input."""
    try:
        from ..core.audio_monitor import list_input_devices
    except Exception:
        return []
    found: list[LoopbackInfo] = []
    for d in list_input_devices():
        for kw, vendor in LOOPBACK_KEYWORDS.items():
            if kw.lower() in d.name.lower():
                found.append(
                    LoopbackInfo(
                        name=d.name, vendor=vendor, device_index=d.index
                    )
                )
                break
    return found


def is_blackhole_installed_via_brew() -> Optional[str]:
    """Trả về version BlackHole nếu đã cài qua brew."""
    if shutil.which("brew") is None:
        return None
    try:
        out = subprocess.run(
            ["brew", "list", "--versions", "blackhole-2ch"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        return None
    return None


def install_instructions_html() -> str:
    """HTML hướng dẫn user cài BlackHole + setup Multi-Output Device."""
    return """
<h3>Vì sao cần?</h3>
<p>macOS bảo mật không cho app capture âm thanh đang phát ra loa. Phải dùng
một <b>virtual audio driver</b> route output → input ảo, sau đó tool đọc
input ảo đó.</p>

<h3>1. Cài BlackHole 2ch (free)</h3>
<p>Mở Terminal chạy:</p>
<p><code>brew install blackhole-2ch</code></p>
<p>Hoặc tải trực tiếp tại <a href="https://existential.audio/blackhole/">
existential.audio/blackhole</a> rồi chạy installer.</p>
<p>Cài xong, BlackHole sẽ xuất hiện trong danh sách audio device.</p>

<h3>2. Tạo Multi-Output Device để vẫn nghe được loa</h3>
<p>Nếu chỉ chọn BlackHole làm output thì mày không nghe gì cả (output đi
hết vào input ảo). Tạo Multi-Output để nghe loa <b>và</b> đẩy vào BlackHole
song song:</p>
<ol>
  <li>Mở <code>Audio MIDI Setup</code> (<code>open -a "Audio MIDI Setup"</code>)</li>
  <li>Bấm dấu <b>+</b> góc dưới trái → <b>Create Multi-Output Device</b></li>
  <li>Tick cả <b>BlackHole 2ch</b> và <b>loa của mày</b> (vd MacBook Speakers)</li>
  <li>Đặt loa làm <b>Master Device</b>, bật Drift Correction cho loa</li>
</ol>

<h3>3. Set Multi-Output làm system output</h3>
<ol>
  <li>System Settings → Sound → Output → chọn <b>Multi-Output Device</b></li>
  <li>Hoặc click vào loa ở thanh menu, chọn Multi-Output</li>
</ol>

<h3>4. Trong tool này</h3>
<ol>
  <li>Mở <b>Tools → Audio Monitor</b> hoặc <b>Test match</b> ở Audio library</li>
  <li>Chọn input là <b>BlackHole 2ch</b></li>
  <li>Phát thử âm thanh - mày sẽ thấy RMS / confidence nhảy</li>
</ol>

<h3>Lưu ý</h3>
<ul>
  <li>Chỉ những app dùng audio output mặc định mới đi qua Multi-Output. App
  có audio routing riêng có thể không bắt được.</li>
  <li>Khi không cần test/auto, đổi system output về loa để âm thanh nghe
  rõ chuẩn (Multi-Output có thể hơi delay).</li>
  <li>Loopback của Rogue Amoeba ($99) cho phép route audio per-app, chuyên
  nghiệp hơn nhưng tốn tiền.</li>
</ul>
"""
