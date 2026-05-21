"""Test mở stream BlackHole để xem có lỗi gì."""
import time
import numpy as np
import sounddevice as sd


def test(device, sr):
    counts = [0]
    def cb(indata, frames, time_info, status):
        counts[0] += frames
        if status:
            print('  status:', status)

    print(f'Try device={device} sr={sr}...')
    try:
        s = sd.InputStream(
            device=device, channels=1, samplerate=sr,
            dtype='float32', blocksize=max(256, sr // 20), callback=cb,
        )
        s.start()
        time.sleep(0.5)
        s.stop()
        s.close()
        print(f'  OK got {counts[0]} frames')
    except Exception as e:
        print(f'  FAIL: {e}')
    print()


def main():
    print('Test BlackHole 2ch (default) at various sample rates:')
    for sr in [16000, 22050, 44100, 48000]:
        test(0, sr)

    print('Test with device=None (system default):')
    for sr in [16000, 48000]:
        test(None, sr)


if __name__ == "__main__":
    main()
