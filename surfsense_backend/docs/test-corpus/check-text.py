"""Kiểm tra từng PDF trong bộ văn bản có trích xuất được chữ hay không.

File nào báo 0 ký tự là bản scan ảnh -> SurfSense sẽ cần OCR để đọc,
và nếu ETL không bật OCR thì tài liệu đó sẽ được lưu với 0 chunk.

Dùng:  pip install pypdf && python check-text.py <thư-mục>
"""

import glob
import logging
import os
import re
import sys
import warnings

logging.disable(logging.CRITICAL)
warnings.filterwarnings("ignore")

from pypdf import PdfReader  # noqa: E402


def main(folder: str) -> int:
    files = sorted(glob.glob(os.path.join(folder, "*.pdf")))
    if not files:
        print(f"Không thấy PDF nào trong {folder}")
        return 1
    scanned = []
    for path in files:
        name = os.path.basename(path)
        try:
            reader = PdfReader(path)
            text = "".join((page.extract_text() or "") for page in reader.pages)
            text = re.sub(r"\s+", " ", text)
        except Exception as exc:  # bản scan hỏng, PDF lỗi...
            print(f"  LOI  {name[:52]:52s} {exc}")
            scanned.append(name)
            continue
        pages = len(reader.pages)
        per_page = len(text) // max(pages, 1)
        flag = "  scan (can OCR)" if per_page < 200 else ""
        if flag:
            scanned.append(name)
        print(f"  {name[:52]:52s} {pages:4d} trang {len(text):8d} ky tu{flag}")

    print()
    if scanned:
        print(f"{len(scanned)}/{len(files)} file là bản scan, cần OCR:")
        for name in scanned:
            print(f"  - {name}")
    else:
        print(f"Tất cả {len(files)} file đều trích xuất được chữ.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "files"))
