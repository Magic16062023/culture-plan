"""Create a shareable QR image for the published project page."""

import sys
from pathlib import Path

import qrcode


def main():
    url = sys.argv[1]
    output = Path(sys.argv[2])
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=12, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    qr.make_image(fill_color="#184f43", back_color="white").save(output)
    print(f"QR-код сохранён: {output} ({url})")


if __name__ == "__main__":
    main()
