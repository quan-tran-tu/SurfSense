# Câu hỏi kiểm thử

Mọi dữ kiện trong cột "Đáp án đúng" đều đã được đối chiếu với text trích xuất từ
chính các file PDF trong bộ, không phải nhớ lại. Cột "Phải trích dẫn" là đáp án
chuẩn về nguồn: trả lời đúng nhưng dẫn sai văn bản thì đó là ăn may, không phải
truy hồi tốt.

Các kịch bản xếp theo độ khó tăng dần. Kịch bản **3, 4, 6, 7** là những kịch bản
thật sự làm hệ RAG gãy — chạy trước nếu không có nhiều thời gian.

---

## 1. Nền — một văn bản, một đoạn

Xác nhận tra cứu thẳng chạy được, trước khi đổ lỗi cho thứ khác.

| # | Câu hỏi | Đáp án đúng | Phải trích dẫn |
|---|---|---|---|
| 1.1 | Luật An ninh mạng 2018 có hiệu lực từ khi nào? | 01/01/2019. | 01 |
| 1.2 | Hệ thống thông tin cấp độ 5 được xác định theo tiêu chí nào? | Hệ thống xử lý thông tin bí mật nhà nước, hoặc hệ thống phục vụ quốc phòng, an ninh quốc gia… theo Nghị định 85/2016/NĐ-CP. | 13 |
| 1.3 | Người dùng mạng xã hội phải xác thực tài khoản bằng gì? | Bằng số điện thoại di động tại Việt Nam; chỉ khi người dùng xác nhận không có số điện thoại di động thì mới dùng phương thức khác. | 12 |

## 2. Một thực thể, nhiều văn bản

Số văn bản dưới đây là số đếm thật trên bộ 18 văn bản.

| # | Câu hỏi | Đáp án đúng | Phải trích dẫn |
|---|---|---|---|
| 2.1 | Bộ Công an có những thẩm quyền gì trong toàn bộ các văn bản này? | Bộ Công an xuất hiện ở 13/18 văn bản. Câu trả lời tốt phải nhóm theo mảng: an ninh mạng (01, 02), bảo vệ dữ liệu cá nhân (08, 09, 10), quản lý internet (11, 12), dữ liệu (04)… | ≥5 trong: 01, 02, 03, 04, 05, 08, 09, 10, 11, 12, 13, 16, 17 |
| 2.2 | "Lực lượng chuyên trách bảo vệ an ninh mạng" được nhắc ở những văn bản nào và với thẩm quyền gì? | Chỉ 3 văn bản: 01, 02 (thẩm quyền yêu cầu ngừng cung cấp dịch vụ, yêu cầu xóa thông tin) và 10 (chế tài khi không chấp hành). | 01, 02, 10 |
| 2.3 | Ban Cơ yếu Chính phủ có vai trò gì? | Xuất hiện ở 8 văn bản, đậm nhất ở mật mã dân sự (15) và Luật ATTTM (03). | ≥3 trong: 01, 02, 03, 10, 11, 12, 15, 17 |
| 2.4 | Quy định về mật mã dân sự nằm ở đâu? | 4 văn bản: 03 (Luật ATTTM, chương riêng), 15 (NĐ 58/2016 — chi tiết nhất), 14, và 02. | 03 và 15 bắt buộc |

**Cần để ý:** hệ thống lấy top-k theo độ tương đồng sẽ trả lời 2.1 chỉ bằng Luật
An ninh mạng rồi dừng. Đó chính là kiểu hỏng cần bắt: một câu trả lời tự tin,
trông đúng, một nguồn, cho câu hỏi trải trên 13 văn bản.

## 3. Trùng số — bẫy mốc thời gian

"72 giờ" xuất hiện ở 5 văn bản với **5 nghĩa vụ hoàn toàn khác nhau**. "24 giờ"
xuất hiện ở 4 văn bản. Đây là kịch bản chẩn đoán mạnh nhất trong file này.

| # | Câu hỏi | Đáp án đúng | Phải trích dẫn |
|---|---|---|---|
| 3.1 | Phát hiện vi phạm quy định bảo vệ dữ liệu cá nhân thì phải thông báo trong bao lâu? | 72 giờ kể từ khi phát hiện hành vi vi phạm (Luật BVDLCN 2025, Điều 23). Nếu bên xử lý phát hiện thì phải báo kịp thời cho bên kiểm soát. | 05 (và 09 cho quy định chi tiết) |
| 3.2 | "72 giờ" trong Luật An ninh mạng 2018 là thời hạn cho việc gì? | **Không phải** thông báo vi phạm dữ liệu — đó là thời hạn liên quan tới yêu cầu quản lý nhà nước về an ninh mạng / hết thời hạn khắc phục điểm yếu, lỗ hổng bảo mật theo khuyến cáo. | 01 |
| 3.3 | Liệt kê tất cả nghĩa vụ "trong 72 giờ" trong bộ văn bản này, ai phải làm gì với ai. | Ít nhất 5 nghĩa vụ khác nhau: 01 khắc phục lỗ hổng; 05 thông báo vi phạm BVDLCN; 08 thực hiện quyền hạn chế xử lý / quyền của chủ thể dữ liệu sau khi nhận yêu cầu; 09 thông báo lộ, mất dữ liệu nhạy cảm (tài chính, ngân hàng, thông tin tín dụng); 10 mô tả hành vi bị xử phạt. | 01, 05, 08, 09, 10 |
| 3.4 | Doanh nghiệp phải ngừng cung cấp dịch vụ trong bao lâu khi có yêu cầu? | 24 giờ kể từ thời điểm có yêu cầu của lực lượng chuyên trách bảo vệ an ninh mạng thuộc Bộ Công an (và của cơ quan có thẩm quyền của Bộ TT&TT theo Luật 2018), đồng thời lưu nhật ký hệ thống. | 01 hoặc 02 |
| 3.5 | Thời hạn gỡ bỏ nội dung vi phạm trên mạng xã hội là bao lâu? | 24 giờ kể từ khi có yêu cầu bằng văn bản hoặc qua phương tiện điện tử của Bộ TT&TT, Bộ Công an hoặc cơ quan có thẩm quyền. | 12 |

Trả lời sai điển hình: gộp "72 giờ" của Luật ANM 2018 vào nghĩa vụ thông báo vi
phạm dữ liệu cá nhân, hoặc trộn mốc 24 giờ của Luật ANM với mốc 24 giờ gỡ nội dung
của Nghị định 147/2024.

## 4. Hết hiệu lực và thay thế

Bộ này có 4 cặp thay thế thật. Đây là phép thử có hướng: **văn bản mới mang dữ kiện
đó, văn bản cũ thì không** — một văn bản đã hết hiệu lực không tự biết mình đã hết
hiệu lực.

| # | Câu hỏi | Đáp án đúng | Phải trích dẫn |
|---|---|---|---|
| 4.1 | Luật An ninh mạng 2018 còn hiệu lực không? | Không. Luật An ninh mạng 116/2025/QH15 có hiệu lực từ 01/7/2026, và Luật 24/2018/QH14 hết hiệu lực kể từ ngày đó. | **02** (trích 01 là sai hướng) |
| 4.2 | Luật An ninh mạng 2025 thay thế những luật nào? | **Hai** luật: Luật An toàn thông tin mạng 86/2015/QH13 (đã sửa đổi theo Luật 35/2018/QH14) **và** Luật An ninh mạng 24/2018/QH14. Hai đạo luật nhập làm một. | 02 |
| 4.3 | Nghị định 13/2023/NĐ-CP còn áp dụng không? | Không. Nghị định 356/2025/NĐ-CP có hiệu lực từ 01/01/2026 và làm NĐ 13/2023 hết hiệu lực. | **09** |
| 4.4 | Nghị định nào bãi bỏ Nghị định 72/2013/NĐ-CP? | Nghị định 147/2024/NĐ-CP (hiệu lực 25/12/2024), bãi bỏ NĐ 72/2013, NĐ 27/2018 và Điều 2 NĐ 150/2018. | **12 — phần p2** |
| 4.5 | Sắp xếp các văn bản này theo ngày có hiệu lực, từ sớm nhất. | 11 (01/9/2013) → 13, 14, 15 (01/7/2016) → 17 (ngày ký, 2017) → 01 (01/01/2019) → 08 (01/7/2023) → 06 (01/7/2024) → 12 (25/12/2024) → 04 (01/7/2025) → 16 (15/8/2025) → 05, 09 (01/01/2026) → 02 (01/7/2026) → 10 (19/8/2026) | ≥8 văn bản |

4.4 còn là phép thử về file bị chia: dữ kiện nằm ở **phần 2** của Nghị định
147/2024, không có trong phần 1. Nếu chỉ phần 1 được index tốt, câu này sẽ trượt.

## 5. So sánh định nghĩa giữa các văn bản

| # | Câu hỏi | Đáp án đúng | Phải trích dẫn |
|---|---|---|---|
| 5.1 | "Dữ liệu cá nhân nhạy cảm" gồm những gì và được quy định ở đâu? | 4 văn bản có khái niệm này: 05 (luật), 08 (nghị định cũ), 09 (nghị định hiện hành, chi tiết nhất), 10 (chế tài). Câu trả lời tốt phải nói rõ 08 đã hết hiệu lực. | 05, 09 (và nêu 08 đã hết hiệu lực) |
| 5.2 | Quy định về chuyển dữ liệu cá nhân ra nước ngoài khác nhau thế nào giữa Nghị định 13/2023 và quy định hiện hành? | NĐ 13/2023 quy định hồ sơ đánh giá tác động chuyển dữ liệu ra nước ngoài; hiện hành là Luật 91/2025 + NĐ 356/2025. | 08 **và** 05/09 |
| 5.3 | Đánh giá tác động xử lý dữ liệu cá nhân là gì, ai phải làm? | Có ở 4 văn bản (05, 08, 09, 10); nghĩa vụ hiện hành theo 05 và 09. | 05, 09 |

## 6. Trùng số điều, trùng tiêu đề

Điều 23 với đúng tiêu đề **"Thông báo vi phạm quy định về bảo vệ dữ liệu cá nhân"**
tồn tại trong **cả hai** văn bản 05 (Luật 91/2025) và 08 (NĐ 13/2023 — đã hết hiệu
lực). Đây là phép thử phân biệt thực thể, không phải phép thử recall.

| # | Câu hỏi | Đáp án đúng | Phải trích dẫn |
|---|---|---|---|
| 6.1 | Điều 23 quy định gì? | Câu hỏi mơ hồ. Cách trả lời đúng là hỏi lại "Điều 23 của văn bản nào?", hoặc trả lời cho từng văn bản và nói rõ NĐ 13/2023 đã hết hiệu lực. | — |
| 6.2 | Nêu nội dung Điều 23 về thông báo vi phạm bảo vệ dữ liệu cá nhân. | Phải nêu được có hai văn bản cùng số điều, cùng tiêu đề, và văn bản đang có hiệu lực là Luật 91/2025. | 05 **và** 08 |
| 6.3 | Nghĩa vụ thông báo vi phạm dữ liệu cá nhân hiện nay theo quy định nào? | Luật 91/2025 (Điều 23) và NĐ 356/2025. **Không** được trả lời bằng NĐ 13/2023. | 05, 09 |

Hệ thống trả lời 6.3 bằng Nghị định 13/2023 là đã đưa ra một câu trả lời sai về mặt
pháp lý mà vẫn trích dẫn "đúng" nguồn — kiểu sai nguy hiểm nhất trong tra cứu pháp
luật, và không có phép thử một-văn-bản nào phát hiện được.

## 7. Câu hỏi không có đáp án trong bộ

Chạy hết. Hệ thống không bao giờ nói "không có" thì không dùng được cho OSINT.

| # | Câu hỏi | Hành vi đúng |
|---|---|---|
| 7.1 | GDPR quy định thời hạn thông báo vi phạm dữ liệu là bao lâu? | Nói rõ GDPR không có trong bộ tài liệu. Không được trả lời bằng kiến thức nền mà không nói rõ. |
| 7.2 | Tóm tắt Điều 250 Luật An ninh mạng 2018. | Luật An ninh mạng 2018 chỉ có 43 điều. Phải nói không có điều đó, không được bịa. |
| 7.3 | Mức thuế suất thuế thu nhập doanh nghiệp với công ty công nghệ là bao nhiêu? | Không thuộc phạm vi bộ văn bản. |
| 7.4 | Văn bản nào trong bộ này điều chỉnh tiền mã hóa? | Không có văn bản nào. Luật Dữ liệu và Luật Giao dịch điện tử là lĩnh vực liền kề nhưng khác. |

## 8. Phạm vi thư mục

Cần bố trí 4 thư mục như trong README. Cùng một câu hỏi, khác phạm vi, đáp án
**phải** khác.

| # | Câu hỏi | Phạm vi | Kỳ vọng |
|---|---|---|---|
| 8.1 | Có những nghĩa vụ thông báo, báo cáo nào trong thời hạn 72 giờ? | thư mục `du-lieu-ca-nhan` | Thông báo vi phạm BVDLCN (05, 09), chế tài (10). **Không** được dẫn Luật An ninh mạng. |
| 8.2 | Có những nghĩa vụ thông báo, báo cáo nào trong thời hạn 72 giờ? | thư mục `an-ninh-mang` | Chỉ nội dung của 01 (khắc phục lỗ hổng). **Không** được dẫn Luật BVDLCN. |
| 8.3 | Có những nghĩa vụ thông báo, báo cáo nào trong thời hạn 72 giờ? | toàn bộ space | Cả hai nhóm, phân biệt rõ. |
| 8.4 | Cơ quan nào có thẩm quyền xử lý? | thư mục `an-toan-thong-tin` | Bộ TT&TT, Ban Cơ yếu Chính phủ. Không có nội dung xử phạt VPHC về dữ liệu cá nhân. |

8.1 dẫn ra văn bản ngoài thư mục nghĩa là phạm vi thư mục đang rò. 8.3 trả lời
giống hệt 8.1 nghĩa là truy vấn toàn space đang bị thu hẹp ngầm.

## 9. Mức phạt — tổng hợp và số học

| # | Câu hỏi | Đáp án đúng | Phải trích dẫn |
|---|---|---|---|
| 9.1 | Mức phạt tiền tối đa trong lĩnh vực an ninh mạng là bao nhiêu? | 200.000.000 đồng với tổ chức, 100.000.000 đồng với cá nhân. | 10 |
| 9.2 | Mua bán dữ liệu cá nhân bị phạt thế nào? | Tối đa 10 lần khoản thu có được từ hành vi vi phạm; nếu không có khoản thu thì áp mức khác theo luật. | 05 (và 10) |
| 9.3 | Phạt theo doanh thu áp dụng cho hành vi nào, tỷ lệ bao nhiêu? | Tối đa 5% doanh thu năm trước liền kề của tổ chức, cho vi phạm về bảo vệ dữ liệu cá nhân. | 05 và 10 |
| 9.4 | Lập bảng so sánh chế tài giữa vi phạm an ninh mạng và vi phạm bảo vệ dữ liệu cá nhân. | An ninh mạng: trần tiền tuyệt đối (200/100 triệu). BVDLCN: theo doanh thu (5%) hoặc theo khoản thu (10 lần) — cơ chế khác hẳn. | 05, 10 |

Chỗ dễ lẫn số: gán 5% doanh thu cho vi phạm an ninh mạng, hoặc biến "10 lần khoản
thu" thành "10% doanh thu".

## 10. Chuỗi nhiều bước

| # | Câu hỏi | Đáp án đúng | Phải trích dẫn |
|---|---|---|---|
| 10.1 | Hệ thống thông tin cấp độ 4 thì phải làm hồ sơ gì, theo hướng dẫn nào, và không làm thì bị phạt ra sao? | Cấp độ theo NĐ 85/2016 (13) → hồ sơ đề xuất cấp độ hướng dẫn tại TT 12/2022 (18) → chế tài khi đưa hệ thống vào vận hành mà chưa được phê duyệt cấp độ 3–5 tại NĐ 330/2026 (10). | 13, 18, 10 |
| 10.2 | Doanh nghiệp nước ngoài phải lưu trữ dữ liệu tại Việt Nam trong bao lâu? | Luật An ninh mạng (01, 02) chỉ nói "trong thời gian theo quy định của Chính phủ" — thời hạn cụ thể nằm ở Nghị định 53/2022 (**văn bản 07, bản scan**). | 01/02 cho nghĩa vụ; 07 cho thời hạn |
| 10.3 | Hệ thống thông tin quan trọng về an ninh quốc gia bị sự cố thì quy trình ứng cứu ra sao? | Danh mục và trách nhiệm ở 01/02; phương án ứng cứu khẩn cấp quốc gia ở QĐ 05/2017 (17). | 01 hoặc 02, **và** 17 |

**10.2 là canary cho ETL.** Nếu OCR không bật, văn bản 07 vào hệ thống với 0 chunk
và câu này sẽ được trả lời cụt bằng "theo quy định của Chính phủ" mà không ai biết
là thiếu. Hành vi đúng khi thiếu: nói rõ thời hạn cụ thể không tìm thấy trong tài
liệu đã nạp.

## 11. Nhớ được sâu trong văn bản dài

Văn bản 10 và 12 là dài nhất (104 và 251 trang).

| # | Câu hỏi | Đáp án đúng | Phải trích dẫn |
|---|---|---|---|
| 11.1 | Vi phạm quy định về thủ tục thực hiện quyền của chủ thể dữ liệu cá nhân bị phạt bao nhiêu? | Phạt tiền từ 10.000.000 đến 20.000.000 đồng với bên kiểm soát dữ liệu cá nhân (Điều 44 NĐ 330/2026). | 10 |
| 11.2 | Trung tâm dữ liệu quốc gia được quy định ở đâu? | Chủ yếu ở Luật Dữ liệu (04), có nhắc trong NĐ 330/2026 (10). Chỉ 2/18 văn bản. | 04 |
| 11.3 | Điều kiện cấp phép cung cấp dịch vụ trò chơi điện tử G1 trên mạng gồm những gì? | Hồ sơ đề nghị cấp Quyết định phát hành trò chơi G1 theo NĐ 147/2024. | 12 |

## 12. Đa ngôn ngữ

Bộ văn bản tiếng Việt. Các câu hỏi tiếng Anh dưới đây kiểm tra chiều ngược lại của
kịch bản 13 trong bộ EU.

| # | Câu hỏi | Kỳ vọng |
|---|---|---|
| 12.1 | What is the deadline for notifying a personal data breach under Vietnamese law? | 72 giờ, dẫn 05. |
| 12.2 | Which Vietnamese law replaced the 2018 Cybersecurity Law? | Luật 116/2025/QH15, dẫn 02. |
| 12.3 | What is the maximum administrative fine for cybersecurity violations in Vietnam? | 200 triệu đồng (tổ chức) / 100 triệu (cá nhân), dẫn 10. |

Nếu 12.1 chạy được mà 12.2 không, nhánh full-text search đang không đóng góp gì —
đúng cái cần biết trước khi demo.

---

## Cách chấm

Với mỗi câu ghi lại: đáp án đúng (có/không), trích dẫn đủ (có/không), trích dẫn
đúng (có/không), có bịa nội dung (có/không).

Con số đáng quan tâm không phải tỷ lệ đúng, mà là **có bao nhiêu câu cho ra đáp án
tự tin nhưng sai**. Kịch bản 3, 4, 6, 7 và 10 được thiết kế để tạo ra đúng loại lỗi
đó — và đó là loại lỗi người dùng không tự phát hiện được.
