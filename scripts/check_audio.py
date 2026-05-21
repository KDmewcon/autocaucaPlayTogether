"""Liệt kê audio devices để check BlackHole có còn không."""
import sounddevice as sd


def main() -> None:
    print('Default input device:', sd.default.device)
    print()
    print('Input devices:')
    for i, d in enumerate(sd.query_devices()):
        if d['max_input_channels'] > 0:
            sr = d.get('default_samplerate', 0)
            print(f"  [{i}] {d['name']}   in={d['max_input_channels']}ch   sr={sr:.0f}")

    print()
    print('Output devices:')
    for i, d in enumerate(sd.query_devices()):
        if d['max_output_channels'] > 0:
            print(f"  [{i}] {d['name']}   out={d['max_output_channels']}ch")


if __name__ == "__main__":
    main()
