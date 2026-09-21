# Bộ văn bản kiểm thử — pháp luật Việt Nam về an ninh mạng & dữ liệu

18 văn bản quy phạm pháp luật thật, cùng một lĩnh vực, liên kết chặt với nhau.
Bộ này được chọn để cùng một thực thể, cùng một mốc thời gian và cùng một con số
xuất hiện lặp lại ở nhiều văn bản — đó là điều kiện để chạy được các kịch bản khó
trong [`queries.md`](queries.md).

## Tải về

```bash
bash surfsense_backend/docs/test-corpus/download.sh          # -> ./files/
bash surfsense_backend/docs/test-corpus/download.sh /duong/dan
```

20 file PDF (văn bản số 12 được đăng làm 3 kỳ Công báo), 838 trang, ~1,53 triệu ký
tự. Toàn bộ link đã kiểm chứng chạy được ngày 08/09/2026.

Script **không hardcode link file**: nó mở trang văn bản trên Công báo rồi lấy link
tải thật, vì một phần link CDN có chữ ký và sẽ đổi theo thời gian. Nếu một văn bản
được đăng trên nhiều số Công báo, script tải đủ các phần và đặt tên `-p1`, `-p2`…

Nguồn: `congbao.chinhphu.vn` (Công báo điện tử) và `vanban.chinhphu.vn`. Hai nguồn
phổ biến khác không dùng được: `vbpl.vn` trả 403 cho script, `thuvienphapluat.vn`
bắt đăng nhập.

## Kiểm tra trước khi nạp

```bash
pip install pypdf && python surfsense_backend/docs/test-corpus/check-text.py files
```

19/20 file là PDF chữ thật (trích xuất được text, đủ dấu tiếng Việt) nên đi qua
ETL bình thường. **Đúng một file là bản scan:**
`07-nd-53-2022-chi-tiet-luat-an-ninh-mang.pdf` — 41 trang, 0 ký tự trích xuất được.

Giữ nguyên file đó là cố ý. Nó là "canary" cho ETL: nếu OCR không bật, văn bản này
sẽ được lưu thành document **0 chunk** — đúng cái lỗi im lặng đã gặp trước đây. Sau
khi nạp xong, hãy kiểm tra số chunk của từng document chứ đừng chỉ đếm số dòng
document.

## Danh sách văn bản

| # | Văn bản | Số hiệu | Thư mục |
|---|---|---|---|
| 01 | Luật An ninh mạng | 24/2018/QH14 | an-ninh-mang |
| 02 | Luật An ninh mạng *(thay thế 24/2018 và 86/2015)* | 116/2025/QH15 | an-ninh-mang |
| 03 | Luật An toàn thông tin mạng | 86/2015/QH13 | an-toan-thong-tin |
| 04 | Luật Dữ liệu | 60/2024/QH15 | du-lieu-internet |
| 05 | Luật Bảo vệ dữ liệu cá nhân | 91/2025/QH15 | du-lieu-ca-nhan |
| 06 | Luật Giao dịch điện tử | 20/2023/QH15 | du-lieu-internet |
| 07 | NĐ quy định chi tiết Luật An ninh mạng *(bản scan)* | 53/2022/NĐ-CP | an-ninh-mang |
| 08 | NĐ bảo vệ dữ liệu cá nhân *(đã hết hiệu lực)* | 13/2023/NĐ-CP | du-lieu-ca-nhan |
| 09 | NĐ quy định chi tiết Luật BVDLCN | 356/2025/NĐ-CP | du-lieu-ca-nhan |
| 10 | NĐ xử phạt VPHC về an ninh mạng và BVDLCN | 330/2026/NĐ-CP | du-lieu-ca-nhan |
| 11 | NĐ quản lý, cung cấp, sử dụng dịch vụ Internet *(đã bị bãi bỏ)* | 72/2013/NĐ-CP | du-lieu-internet |
| 12 | NĐ quản lý, cung cấp, sử dụng dịch vụ Internet *(3 phần)* | 147/2024/NĐ-CP | du-lieu-internet |
| 13 | NĐ bảo đảm an toàn hệ thống thông tin theo cấp độ | 85/2016/NĐ-CP | an-toan-thong-tin |
| 14 | NĐ điều kiện kinh doanh sản phẩm, dịch vụ ATTT mạng | 108/2016/NĐ-CP | an-toan-thong-tin |
| 15 | NĐ về mật mã dân sự | 58/2016/NĐ-CP | an-toan-thong-tin |
| 16 | NĐ mức hỗ trợ người làm chuyên trách CĐS, ATTTM, ANM | 179/2025/NĐ-CP | an-ninh-mang |
| 17 | QĐ hệ thống phương án ứng cứu khẩn cấp ATTT mạng quốc gia | 05/2017/QĐ-TTg | an-ninh-mang |
| 18 | TT hướng dẫn Nghị định 85/2016/NĐ-CP | 12/2022/TT-BTTTT | an-toan-thong-tin |

## Cách bố trí để chạy đủ kịch bản

Một search space `Pháp luật ANM & Dữ liệu`, bên trong chia 4 thư mục đúng như cột
cuối bảng trên:

| Thư mục | Văn bản |
|---|---|
| `an-ninh-mang` | 01, 02, 07, 16, 17 |
| `du-lieu-ca-nhan` | 05, 08, 09, 10 |
| `an-toan-thong-tin` | 03, 13, 14, 15, 18 |
| `du-lieu-internet` | 04, 06, 11, 12 |

Kịch bản 8 trong `queries.md` phụ thuộc vào cách chia này: cùng một câu hỏi, hỏi
trong hai thư mục khác nhau thì câu trả lời **phải** khác nhau.

Nạp theo thứ tự nào cũng được, nhưng chờ index xong hẳn rồi mới hỏi.

## Bộ luật EU (tùy chọn)

`download-eu.sh` / `queries-eu.md` / `README-eu.md` là bộ 19 đạo luật số của EU
bằng tiếng Anh, dạng HTML (đi qua nhánh `DIRECT_CONVERT`, không cần OCR). Dùng khi
muốn tách bạch chất lượng truy hồi khỏi chất lượng OCR, hoặc để so sánh kết quả
tiếng Việt với tiếng Anh trên cùng một loại kịch bản.
