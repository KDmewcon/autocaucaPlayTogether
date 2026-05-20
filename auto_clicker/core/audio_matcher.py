"""Audio matcher - phát hiện 1 đoạn audio reference (mp3/wav) trong stream live.

Approach: mel-spectrogram fingerprint + normalized cross-correlation.

Pipeline:
1. Load reference (mp3/wav) -> mono, downsample 16kHz, normalize.
2. Tính log-mel-spectrogram (~32 mel band, hop 23ms).
3. Stream live audio cũng tính log-mel-spec dạng rolling buffer.
4. Match: cross-correlation 2D giữa ref-spec và buffer-spec, normalized.
5. Nếu max correlation >= threshold trong cửa sổ thời gian -> matched.

So với simple RMS: pattern này chịu được volume khác nhau, noise nhẹ, vẫn nhận
đúng đoạn mp3 cụ thể (vd: tiếng "ding" của thông báo, tiếng nhạc nền 1 đoạn).

So với Shazam fingerprint: đơn giản hơn nhưng đủ tốt cho clip ngắn (<= 5s).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

try:
    import soundfile as sf

    _HAS_SF = True
except Exception:  # pragma: no cover
    _HAS_SF = False

try:
    import audioread

    _HAS_AR = True
except Exception:  # pragma: no cover
    _HAS_AR = False

try:
    from scipy.signal import resample_poly

    _HAS_SCIPY = True
except Exception:  # pragma: no cover
    _HAS_SCIPY = False


# Standard params cho fingerprint
SAMPLE_RATE = 16000
N_FFT = 512
HOP_SIZE = 256  # ~16ms ở 16kHz
N_MELS = 32
F_MIN = 80.0
F_MAX = 8000.0


def _hz_to_mel(hz: float) -> float:
    return 2595.0 * np.log10(1.0 + hz / 700.0)


def _mel_to_hz(mel: float) -> float:
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)


def _build_mel_filterbank(sr: int, n_fft: int, n_mels: int, fmin: float, fmax: float) -> np.ndarray:
    """Trả về matrix [n_mels, n_fft//2+1] map từ |STFT|^2 -> mel-spec."""
    n_freqs = n_fft // 2 + 1
    mels = np.linspace(_hz_to_mel(fmin), _hz_to_mel(fmax), n_mels + 2)
    hzs = np.array([_mel_to_hz(m) for m in mels])
    bins = np.floor((n_fft + 1) * hzs / sr).astype(int)
    bins = np.clip(bins, 0, n_freqs - 1)
    fb = np.zeros((n_mels, n_freqs), dtype=np.float32)
    for m in range(1, n_mels + 1):
        l, c, r = bins[m - 1], bins[m], bins[m + 1]
        if c == l:
            c = l + 1
        if r == c:
            r = c + 1
        for k in range(l, c):
            fb[m - 1, k] = (k - l) / max(1, (c - l))
        for k in range(c, r):
            fb[m - 1, k] = (r - k) / max(1, (r - c))
    return fb


_MEL_FB = _build_mel_filterbank(SAMPLE_RATE, N_FFT, N_MELS, F_MIN, F_MAX)
_HANN = np.hanning(N_FFT).astype(np.float32)


def compute_log_mel(samples: np.ndarray) -> np.ndarray:
    """Trả về log-mel-spec dạng [n_mels, n_frames]."""
    samples = samples.astype(np.float32, copy=False)
    if samples.size < N_FFT:
        return np.zeros((N_MELS, 0), dtype=np.float32)
    n_frames = 1 + (samples.size - N_FFT) // HOP_SIZE
    if n_frames <= 0:
        return np.zeros((N_MELS, 0), dtype=np.float32)
    idx = (
        np.arange(N_FFT)[None, :]
        + np.arange(n_frames)[:, None] * HOP_SIZE
    )
    frames = samples[idx] * _HANN[None, :]
    spec = np.fft.rfft(frames, n=N_FFT, axis=1)
    spec_re = spec.real.astype(np.float64)
    spec_im = spec.imag.astype(np.float64)
    power = spec_re * spec_re + spec_im * spec_im
    power = np.clip(power, 0.0, 1e10)
    # Tính mel = power @ _MEL_FB.T qua np.dot tránh trigger matmul warning
    mel = np.dot(power, _MEL_FB.T.astype(np.float64))
    mel = mel.astype(np.float32)
    log_mel = np.log(np.clip(mel, 1e-6, 1e10)).astype(np.float32)
    return log_mel.T  # [n_mels, n_frames]


def _normalize(x: np.ndarray) -> np.ndarray:
    """Z-norm trên toàn matrix (subtract mean, divide std)."""
    if x.size == 0:
        return x
    mean = float(x.mean())
    std = float(x.std()) + 1e-6
    return ((x - mean) / std).astype(np.float32)


def load_audio(path: str) -> tuple[np.ndarray, int]:
    """Load audio thành mono float32 ở SAMPLE_RATE.

    Hỗ trợ wav (soundfile) và mp3 (audioread fallback).
    """
    samples: Optional[np.ndarray] = None
    src_sr: int = 0
    # Try soundfile first
    if _HAS_SF:
        try:
            data, src_sr = sf.read(path, dtype="float32", always_2d=False)
            samples = (
                data
                if data.ndim == 1
                else data.mean(axis=1).astype(np.float32)
            )
        except Exception:
            samples = None
    if samples is None:
        if not _HAS_AR:
            raise RuntimeError(
                "Cannot load audio: cần soundfile hoặc audioread."
            )
        with audioread.audio_open(path) as f:
            src_sr = f.samplerate
            ch = f.channels
            buf = bytearray()
            for chunk in f:
                buf.extend(chunk)
            arr = np.frombuffer(bytes(buf), dtype=np.int16).astype(np.float32) / 32768.0
            if ch > 1:
                arr = arr.reshape(-1, ch).mean(axis=1)
            samples = arr.astype(np.float32)

    # Resample về SAMPLE_RATE
    if src_sr != SAMPLE_RATE:
        if _HAS_SCIPY:
            from math import gcd

            g = gcd(src_sr, SAMPLE_RATE)
            up = SAMPLE_RATE // g
            down = src_sr // g
            samples = resample_poly(samples, up, down).astype(np.float32)
        else:
            # Fallback: linear interpolate (kém hơn nhưng vẫn dùng được)
            ratio = SAMPLE_RATE / src_sr
            n_out = int(samples.size * ratio)
            x_old = np.linspace(0, 1, samples.size)
            x_new = np.linspace(0, 1, n_out)
            samples = np.interp(x_new, x_old, samples).astype(np.float32)

    # Normalize amplitude
    peak = float(np.max(np.abs(samples)) + 1e-9)
    if peak > 0:
        samples = samples / peak * 0.95
    return samples, SAMPLE_RATE


@dataclass
class AudioPattern:
    """Reference audio đã pre-compute fingerprint."""

    pattern_id: str
    name: str
    path: str
    duration_s: float
    log_mel: np.ndarray  # [n_mels, n_frames]
    log_mel_norm: np.ndarray  # z-normalized

    @property
    def n_frames(self) -> int:
        return self.log_mel.shape[1]


def build_pattern(pattern_id: str, name: str, path: str) -> AudioPattern:
    samples, _ = load_audio(path)
    log_mel = compute_log_mel(samples)
    if log_mel.shape[1] < 4:
        raise RuntimeError(
            "File audio quá ngắn để fingerprint (cần ít nhất ~0.1s)"
        )
    return AudioPattern(
        pattern_id=pattern_id,
        name=name,
        path=path,
        duration_s=samples.size / SAMPLE_RATE,
        log_mel=log_mel,
        log_mel_norm=_normalize(log_mel),
    )


@dataclass
class MatchResult:
    matched: bool
    confidence: float  # 0..1


def match_pattern(
    pattern: AudioPattern,
    buffer_log_mel: np.ndarray,
    threshold: float = 0.7,
    stride_factor: int = 8,
) -> MatchResult:
    """Tìm pattern trong buffer log-mel spec bằng cv2.matchTemplate (NCC).

    Tốc độ O(N log N) qua FFT, nhanh hơn ~10-50x so với manual sliding.
    """
    if buffer_log_mel.shape[1] < pattern.n_frames:
        return MatchResult(False, 0.0)

    try:
        import cv2  # opencv đã có trong requirements
        # cv2.matchTemplate cần image 2D float32. Buffer là [n_mels, n_frames].
        # Template là pattern.log_mel cùng shape.
        buf = buffer_log_mel.astype(np.float32, copy=False)
        tpl = pattern.log_mel.astype(np.float32, copy=False)
        # TM_CCOEFF_NORMED ~ tương đương Pearson correlation, output [-1, 1]
        res = cv2.matchTemplate(buf, tpl, cv2.TM_CCOEFF_NORMED)
        # res shape = (n_mels - n_mels + 1, n_frames - n_pat + 1) = (1, K)
        if res.size == 0:
            return MatchResult(False, 0.0)
        best_corr = float(res.max())
    except Exception:
        # Fallback: dot product manual (không nên xảy ra vì cv2 luôn có)
        best_corr = _match_pattern_manual(pattern, buffer_log_mel, stride_factor)

    score = max(0.0, best_corr)
    return MatchResult(score >= threshold, score)


def _match_pattern_manual(
    pattern: AudioPattern, buffer_log_mel: np.ndarray, stride_factor: int = 8
) -> float:
    """Fallback nếu cv2 không khả dụng."""
    pat = pattern.log_mel_norm.astype(np.float32)
    pat_flat = pat.ravel()
    n_pat = pat.shape[1]
    n_buf = buffer_log_mel.shape[1]
    pat_norm = float(np.linalg.norm(pat_flat) + 1e-9)
    best_corr = -1.0
    stride = max(1, n_pat // max(1, stride_factor))
    for start in range(0, n_buf - n_pat + 1, stride):
        win = buffer_log_mel[:, start : start + n_pat].astype(np.float32)
        wm = float(win.mean())
        ws = float(win.std()) + 1e-6
        wn = ((win - wm) / ws).ravel()
        wn_norm = float(np.linalg.norm(wn) + 1e-9)
        corr = float(np.dot(pat_flat, wn) / (pat_norm * wn_norm))
        if corr > best_corr:
            best_corr = corr
    return best_corr


class AudioStreamBuffer:
    """Buffer rolling chứa N giây audio gần nhất, có thể tính log-mel theo yêu cầu.

    Tối ưu: cache log-mel của lần snapshot trước, chỉ compute lại khi buffer
    có data MỚI quan trọng. Việc này đặc biệt hữu ích vì poll interval (~50ms)
    nhỏ hơn nhiều so với chiều dài buffer (5s).
    """

    def __init__(self, capacity_seconds: float = 5.0):
        self._capacity = int(capacity_seconds * SAMPLE_RATE)
        self._buf = np.zeros(self._capacity, dtype=np.float32)
        self._write = 0
        self._filled = False
        self._lock = threading.Lock()
        # Tracking version để invalidate cache log-mel
        self._version = 0
        # Cache log-mel
        self._cached_version = -1
        self._cached_log_mel: Optional[np.ndarray] = None

    def append(self, samples: np.ndarray) -> None:
        if samples.ndim > 1:
            samples = samples.mean(axis=1)
        samples = samples.astype(np.float32, copy=False)
        n = samples.size
        if n == 0:
            return
        if n >= self._capacity:
            # Tail
            with self._lock:
                self._buf[:] = samples[-self._capacity :]
                self._write = 0
                self._filled = True
                self._version += 1
            return
        with self._lock:
            end = self._write + n
            if end <= self._capacity:
                self._buf[self._write : end] = samples
            else:
                first = self._capacity - self._write
                self._buf[self._write :] = samples[:first]
                self._buf[: end - self._capacity] = samples[first:]
            self._write = end % self._capacity
            if self._write == 0 or end > self._capacity:
                self._filled = True
            self._version += 1

    def snapshot(self) -> np.ndarray:
        """Trả về copy của buffer ordered theo thời gian (cũ -> mới)."""
        with self._lock:
            if not self._filled:
                return self._buf[: self._write].copy()
            return np.concatenate(
                (self._buf[self._write :], self._buf[: self._write])
            )

    def snapshot_log_mel(self) -> Optional[np.ndarray]:
        """Trả về log-mel của buffer hiện tại, có cache.

        Nếu version không đổi từ lần gọi trước, trả về cache. Nếu thay đổi,
        compute lại và cache. Tránh recompute mỗi poll.
        """
        with self._lock:
            v = self._version
        if v == self._cached_version and self._cached_log_mel is not None:
            return self._cached_log_mel
        snap = self.snapshot()
        if snap.size < N_FFT:
            return None
        lm = compute_log_mel(snap)
        self._cached_version = v
        self._cached_log_mel = lm
        return lm

    def reset(self) -> None:
        with self._lock:
            self._buf.fill(0)
            self._write = 0
            self._filled = False
            self._version += 1
            self._cached_log_mel = None
