#!/usr/bin/env bash
# Tải bộ văn bản pháp luật Việt Nam về an ninh mạng / dữ liệu (18 văn bản)
# từ Công báo điện tử (congbao.chinhphu.vn) và Cơ sở dữ liệu văn bản QPPL
# (vanban.chinhphu.vn). Đây là bản PDF chính thức đăng Công báo.
#
# Script không hardcode link file: nó mở trang văn bản rồi lấy link tải thật,
# vì một phần link CDN có chữ ký và thay đổi theo thời gian.
#
# Dùng:  ./download.sh [thư-mục-đích]        (mặc định: ./files)
set -uo pipefail

OUT="${1:-$(dirname "$0")/files}"
mkdir -p "$OUT"
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

# tên file | trang chứa link tải
DOCS='
01-luat-an-ninh-mang-24-2018-qh14|https://congbao.chinhphu.vn/van-ban/luat-so-24-2018-qh14-26894.htm
02-luat-an-ninh-mang-116-2025-qh15|https://congbao.chinhphu.vn/van-ban/luat-so-116-2025-qh15-468678.htm
03-luat-an-toan-thong-tin-mang-86-2015-qh13|https://congbao.chinhphu.vn/van-ban/luat-so-86-2015-qh13-18386.htm
04-luat-du-lieu-60-2024-qh15|https://congbao.chinhphu.vn/van-ban/luat-so-60-2024-qh15-43563.htm
05-luat-bao-ve-du-lieu-ca-nhan-91-2025-qh15|https://congbao.chinhphu.vn/van-ban/luat-so-91-2025-qh15-45578.htm
06-luat-giao-dich-dien-tu-20-2023-qh15|https://congbao.chinhphu.vn/van-ban/luat-so-20-2023-qh15-39848.htm
07-nd-53-2022-chi-tiet-luat-an-ninh-mang|https://vanban.chinhphu.vn/?pageid=27160&docid=206381
08-nd-13-2023-bao-ve-du-lieu-ca-nhan|https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-13-2023-nd-cp-39228.htm
09-nd-356-2025-chi-tiet-luat-bvdlcn|https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-356-2025-nd-cp-468371.htm
10-nd-330-2026-xu-phat-vphc-anm-bvdlcn|https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-330-2026-nd-cp-470339.htm
11-nd-72-2013-quan-ly-internet|https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-72-2013-nd-cp-2542.htm
12-nd-147-2024-quan-ly-internet|https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-147-2024-nd-cp-43155.htm
13-nd-85-2016-an-toan-htt-theo-cap-do|https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-85-2016-nd-cp-20423.htm
14-nd-108-2016-kinh-doanh-sp-dv-attt|https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-108-2016-nd-cp-20636.htm
15-nd-58-2016-mat-ma-dan-su|https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-58-2016-nd-cp-20017.htm
16-nd-179-2025-ho-tro-nguoi-lam-attt-anm|https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-179-2025-nd-cp-45369.htm
17-qd-05-2017-ung-cuu-khan-cap-attt|https://congbao.chinhphu.vn/van-ban/quyet-dinh-so-05-2017-qd-ttg-22514.htm
18-tt-12-2022-huong-dan-nd-85-2016|https://congbao.chinhphu.vn/van-ban/thong-tu-so-12-2022-tt-btttt-37720.htm
'

# Một văn bản dài có thể đăng trên nhiều số Công báo -> hàm này trả về NHIỀU link.
resolve() {  # in: trang văn bản -> out: các link file thật, mỗi dòng một link
  curl -sL --compressed -A "$UA" --max-time 60 "$1" 2>/dev/null | python -c '
import sys,re,html
t=sys.stdin.read()
out=[]
for pat in (r"https://congbaocdn\.chinhphu\.vn/[^\"'"'"'\s<>]+\.pdf",
            r"https://datafiles\.chinhphu\.vn/cpp/files/vbpq/[^\"'"'"'\s<>]+\.pdf"):
    m=[x for x in dict.fromkeys(re.findall(pat,t)) if "image" not in x and "/Icon/" not in x]
    if m: out=m; break
if not out:
    s=[html.unescape(x) for x in dict.fromkeys(re.findall(r"https://g7\.cdnchinhphu\.vn/api/download/stream\?[^\"'"'"'\s<>]+",t))]
    p=[x for x in s if x.lower().endswith(".pdf")] or s
    out=p[:1]
sys.stdout.write("\n".join(out))
'
}

fail=0
while IFS='|' read -r name page; do
  [ -z "${name// }" ] && continue
  urls=()
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    [ -n "$line" ] && urls+=("$line")
  done < <(resolve "$page")
  if [ "${#urls[@]}" -eq 0 ]; then
    printf '  FAIL %-46s (khong tim duoc link tai)\n' "$name"; fail=1; continue
  fi
  i=0
  for url in "${urls[@]}"; do
    i=$((i+1))
    if [ "${#urls[@]}" -gt 1 ]; then f="$name-p$i.pdf"; else f="$name.pdf"; fi
    curl -sL --compressed -A "$UA" --max-time 180 -o "$OUT/$f" "$url"
    size=$(wc -c < "$OUT/$f" 2>/dev/null | tr -d ' ')
    size="${size:-0}"
    if [ "$(head -c 4 "$OUT/$f" 2>/dev/null)" = "%PDF" ] && [ "$size" -gt 20000 ]; then
      printf '  ok   %-46s %9s bytes\n' "$f" "$size"
    else
      printf '  FAIL %-46s size=%s (khong phai PDF)\n' "$f" "$size"; fail=1
    fi
  done
done <<< "$DOCS"

echo
echo "Bo van ban: $OUT"
echo "Kiem tra file nao khong trich xuat duoc chu (can pypdf):"
echo "  pip install pypdf && python \"$(dirname "$0")/check-text.py\" \"$OUT\""
exit $fail
