# Báo cáo: Dùng và cải thiện SurfSense cho bài toán OSINT

## 1. Bài toán

Công việc OSINT chủ yếu là một quy trình có bốn bước lặp đi lặp lại:

1. **Thu thập** — nhiều thư mục tài liệu (báo cáo, bản tin, tài liệu thu được),
   phần lớn tiếng Việt, đổ vào hệ thống theo từng đợt.
2. **Hỏi có kiểm chứng** — "có thông tin gì về X trong *hai thư mục này* không?".
   Một câu hỏi tồn tại/không tồn tại chỉ có giá trị nếu việc tìm kiếm thực sự
   bị giới hạn đúng trong phạm vi đó.
3. **Viết báo cáo** — kết quả cuối cùng là một văn bản Markdown/PDF có trích dẫn
   truy ngược được về đúng đoạn tài liệu gốc.
4. **Chia sẻ có kiểm soát** — nhiều người cùng làm trên một hệ thống, mỗi người
   một kho riêng, nhưng phải chuyển được một thư mục cho đồng nghiệp mà không
   sao chép dữ liệu và thu hồi được bất cứ lúc nào.

Ràng buộc bao trùm: **dữ liệu không được rời khỏi hạ tầng nội bộ**. Mô hình chạy
bằng vLLM trên GPU tại chỗ; không dùng dịch vụ đám mây cho nội dung tài liệu.

## 2. Vì sao chọn SurfSense

SurfSense đã có sẵn ba tính năng quan trọng:

- **Pipeline Ingestion** — đọc nhiều định dạng tài liệu, tách chunk, sinh
  embedding, chạy nền bằng Celery.
- **Tìm kiếm lai (hybrid search)** — kết hợp vector và full-text Postgres, có
  hạ tầng rerank.
- **Không gian tìm kiếm (search space)** — ranh giới cách ly thật sự, do server
  cưỡng chế, chứ không phải bộ lọc ở tầng giao diện. Đây là nền tảng cho mọi
  cơ chế phân quyền được xây thêm bên trên.
- **Trích dẫn có định danh chunk** — cho phép bấm vào `[3]` và xem lại đúng
  đoạn văn bản đã sinh ra câu trả lời.

Nói cách khác: SurfSense giải quyết tốt phần "kho tri thức" cho bài toán OSINT.

## 3. Các chức năng bổ sung phục vụ bài toán OSINT

### 3.1 Mô hình nội bộ không đủ thông minh để tự quyết định — `simple_rag`

*Triệu chứng:* SurfSense được xây dựng với mục tiêu như là một AI Agent, có khả năng tự quyết định với nhiều loại tool được cung cấp, trong đó có tool phục vụ RAG. Do vậy, SurfSense hoạt động tốt với các mô hình lớn vài trăm tỷ tham số hay là qua các LLM API.

*Giải pháp:* cố định một luồng phục vụ bài toán OSINT: **truy hồi trước, trả lời sau**.
Server tự tìm kiếm, chèn các đoạn văn vào prompt, rồi gọi mô hình **đúng một lần,
không gắn tool nào**. Việc còn lại chỉ là đọc ngữ cảnh được cấp — đúng việc mà
mô hình instruct nhỏ làm tốt. Prompt hệ thống bắt buộc: chỉ dùng đoạn được cấp,
mọi câu đều gắn nhãn `[n]`, không có tài liệu thì nói thẳng là không có. Khi
truy hồi rỗng, server trả về câu thông báo cố định thay vì để mô hình tự "thú
nhận" (mà nó thường không làm).

### 3.2 Câu hỏi cần được mở rộng truy vấn

*Triệu chứng:* các câu hỏi như "tổng hợp các thông tin từ ngày 06.07 đến ngày 13.07" sẽ không thể được đáp ứng bởi pipeline RAG thông thường. Các search candidates trả về khả năng cao sẽ chỉ liên quan tới ngày 06.07 hay ngày 13.07, hoặc định dạng được viết dưới dạng 06/07...

*Giải pháp:* bóc lớp từ mệnh lệnh, giữ
lại thực thể, và **liệt kê từng ngày** trong khoảng đã phát hiện theo nhiều định
dạng. Kết quả đưa vào `keyword_terms`, chỉ nới rộng nhánh từ khóa; nhánh ngữ
nghĩa và reranker vẫn giữ nguyên văn bản gốc nên không bị loãng. Thuần xác định,
không tốn thêm một lần gọi mô hình.

### 3.3 Tài liệu của phiên này không nên lẫn sang phiên khác

*Triệu chứng:* mọi thư mục tải lên đều dùng chung toàn không gian, nên một vụ
việc đang điều tra sẽ "rò" ngữ cảnh sang mọi câu hỏi khác của cùng người dùng.

*Giải pháp:* một cột nullable `folders.owner_thread_id`. `NULL` = dùng chung cả
không gian; có thread id = chỉ phiên đó thấy — trong truy hồi, cây
thư mục, `ls`/`read`/`glob`/`grep` và `@`-mention. Có **thăng cấp** (xóa dấu trên
cả cây con, không copy, không nhúng lại) nhưng không có hạ cấp. Xóa một phiên chat
sẽ *tự động thăng cấp* thư mục của nó thay vì hủy tài liệu.

### 3.4 Chuyển thư mục cho đồng nghiệp — `/share` và `/import`

*Triệu chứng:* search space cách ly tuyệt đối, nên không có đường hợp tác nào cả.

*Giải pháp:* chia sẻ ở mức **thư mục**, bằng **liên kết sống, không sao chép**:
người chia sẻ mint một token, người nhận `/import` và có quyền **đọc** đúng cây
thư mục đó. Không nhân bản tài liệu, nên thu hồi là thu hồi thật (biến mất khỏi
truy hồi ở câu hỏi kế tiếp), chứ không mang tính hình thức.

### 3.5 Báo cáo là sản phẩm cuối, không nên phụ thuộc vào agent

*Triệu chứng:* việc sinh báo cáo đi qua một lượt agent, tức là lại phụ thuộc vào
việc mô hình có chịu gọi tool hay không — cùng một lỗi ở mục 3.1, nhưng hậu quả
nặng hơn vì đầu ra là văn bản chính thức.

*Giải pháp:* tách hẳn đường sinh báo cáo khỏi agent. Prompt lập kế hoạch viết **bằng tiếng Việt** vì kho tài liệu là tiếng Việt. Báo cáo dùng đúng ngữ cảnh truy hồi như một câu hỏi thường.
Kèm theo: `/revise` (sửa lại báo cáo), `/export` (pdf, docx, html, latex,
epub, odt, md), **mẫu báo cáo** (`/report t<id>` — tải lên một báo cáo mẫu và bắt
hệ thống viết theo đúng bố cục đó), và sửa lỗi 500 khi tiêu đề có dấu tiếng Việt.

### 3.6 Vận hành nhiều người dùng — bảng quản trị

Một hệ thống nhiều tài khoản cần chỗ để xử lý sự cố: `app/routes/admin_routes.py`
cho phép liệt kê/khóa/xóa tài khoản, cấp–thu quyền admin, xem và xóa thư mục của
bất kỳ ai, thăng cấp thư mục bị kẹt trong một phiên, và **hard-revoke** một share
— xóa hẳn các `FolderLink`, tức là gỡ thư mục khỏi kho của mọi người đã import,
điều mà revoke thường không làm được. Toàn bộ cưỡng chế ở server (`require_admin`);
nút bấm chỉ ẩn điều khiển chết. Admin đầu tiên seed bằng `ADMIN_EMAILS`.

### 3.7 Các tính năng thêm cho frontend

- **Trích dẫn bấm được**: backend chỉ phân giải `[n]` → `[citation:<chunk id>]`
  lúc *lưu* tin nhắn, không phải trong luồng stream; client vì thế nạp lại tin
  nhắn đã lưu sau khi lượt kết thúc. Dải trích dẫn `[57-61]` được khai triển.
- **Chỉ hiển thị khối văn bản cuối** của một lượt — phần "để tôi tìm trong kho
  tri thức…" không phải câu trả lời.
- **Phiên định danh bằng thread id, không bằng tiêu đề** (backend tự đổi tiêu đề,
  tra theo tên sẽ tạo nhầm phiên rỗng và làm mất lịch sử).

## 4. Hiện trạng và việc còn lại

- top-k retrieval có thể không trả về đủ thông tin liên quan
- Pipeline hiện tại đang áp cố định RAG, không thể xử lý những query mà một AI Agent có thể xử lý với tool phù hợp, như: "có bao nhiêu tài liệu", "có những thư mục nào", ...

