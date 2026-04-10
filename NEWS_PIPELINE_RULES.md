# NEWS_PIPELINE_RULES.md

Luật vận hành cho pipeline bản tin world/VN.

## 1) Runtime luôn động

- Không fix cứng ngày, giờ, thứ, tháng, năm trong:
  - tên file
  - thư mục run
  - voice script
  - metadata
  - Drive path
- Luôn lấy từ runtime env/now.
- Ưu tiên chuẩn:
  - `RUN_DATE`
  - `RUN_HHMM`
  - `RUN_HOUR`
  - fallback = thời gian hiện tại `Asia/Bangkok`

## 2) Một job = một entrypoint rõ ràng

Chỉ dùng các entrypoint chính:
- `run_world_news_dynamic.py`
- `run_vn_news_dynamic.py`
- `news_runtime.py`

Script snapshot/manual cũ phải đưa vào `legacy/`, không dùng cho cron.

## 3) Nội dung phải động, không dùng story cứng

- Không hardcode danh sách story trong pipeline production.
- Story phải lấy từ nguồn động tại thời điểm chạy.
- Nếu không lấy được nguồn đủ tin cậy, fail an toàn, không dựng video giả.

## 4) Anti-repeat là bắt buộc

- Trước khi chọn lineup, đọc metadata các run gần nhất.
- Với bản VN: ưu tiên so với các run cùng ngày; khi cần có thể nhìn thêm hôm trước.
- Nếu một story trùng cụm chủ đề gần đây, bỏ qua nếu vẫn còn lựa chọn đủ tốt khác.
- Anti-repeat áp dụng theo **cụm chủ đề**, không chỉ theo tiêu đề exact match.

## 5) Chế độ thường vs chế độ đặc biệt

### Chế độ thường
- Chạy cron bình thường.
- Không bị khóa vào một chủ đề riêng.
- Tự cân bằng giữa độ mới, độ quan trọng, độ đa dạng.

### Chế độ đặc biệt
Chỉ bật khi có yêu cầu tay hoặc env đặc biệt, ví dụ:
- `FOCUS_KEYWORDS`
- `INCLUDE_YESTERDAY=1`
- `MIN_FOCUS_MATCHES`

Quan trọng:
- Cron mặc định không được vô tình mang theo focus của một lượt test tay.

## 6) Ưu tiên nguồn theo loại bản tin

### VN news
Ưu tiên feed/thẻ mục:
- thời sự
- chính trị
- xã hội
- kinh tế lớn

Giảm ưu tiên hoặc loại bỏ:
- lifestyle
- làm đẹp
- mẹo vặt
- advertorial/PR
- giải trí nhẹ
- bài quá evergreen

### World news
Ưu tiên:
- Reuters
- AP
- BBC
- Bloomberg
- Guardian
- DW
- Al Jazeera
- official sources khi cần

## 7) Focus phải đủ mạnh khi người dùng yêu cầu

Nếu người dùng yêu cầu rõ một cụm chủ đề, ví dụ:
- chức danh mới được bầu
- nhân sự mới
- Quốc hội phê chuẩn

thì pipeline phải:
- ép lineup bám cụm đó mạnh hơn bình thường
- nếu cần, dùng thêm tin hôm qua
- loại bài lệch chủ đề
- chỉ lấp bằng bài gần chủ đề khi không đủ nguồn

## 8) Không xử lý asset cho cả pool ứng viên

- Chỉ download ảnh, xử lý ảnh, render clip cho **story đã được chọn cuối cùng**.
- Không prepare asset cho toàn bộ pool feed.
- Mục tiêu: tiết kiệm thời gian, CPU, token, I/O, tránh lỗi dây chuyền.

## 9) Voice/script phải đúng phong cách

### VN
- mở đầu ngắn
- nói giờ tự nhiên kiểu: `6 giờ`, `12 giờ`, `19 giờ`
- không đọc kiểu `06 giờ 00`
- không nhét thứ/ngày/tháng/năm vào opening nếu không cần

### World
- tương tự: opening ngắn, tự nhiên
- không gắn timestamp/date overlay lên khung hình nếu không có yêu cầu riêng

## 10) Upload phải verify thật

Chỉ được báo success khi đủ cả 4 điều kiện:
1. render xong
2. `rclone copy` thành công
3. `rclone lsf` hoặc verify remote thành công
4. `missing_remote_files == []`

Nếu thiếu bất kỳ điều kiện nào:
- không báo completed thành công
- không xóa local run directory

## 11) Cleanup chỉ sau verify đầy đủ

- Chỉ xóa local khi remote đã verify đủ file.
- Nếu remote chưa đủ hoặc verify lỗi, giữ local để cứu run.

## 12) Summary phải phản ánh thực tế

Summary cuối phải có:
- video filename
- audio duration
- video duration
- run dir
- Drive path
- upload return codes
- missing files
- local deleted hay chưa
- headline đã chọn
- anti-repeat note

Không được báo “uploaded + deleted” nếu thực tế Drive chưa có.

## 13) Khi fail, fail gọn nhưng hữu ích

Failure summary phải nói rõ:
- fail ở bước nào
- còn giữ local không
- có upload dở không
- muốn rerun thì cần gì

## 14) Workspace phải sạch

- file cũ/snapshot/manual đưa vào `legacy/`
- production path phải nhìn vào là biết file nào đang chạy thật
- tránh nhiều script tên gần giống gây nhầm run path

## 15) Khi có yêu cầu mới lặp lại nhiều lần, phải chuẩn hóa lại

Nếu người dùng nhắc đi nhắc lại một rule, ví dụ:
- đừng lặp nội dung
- đúng ngày giờ
- ưu tiên chính trị
- lấy thêm tin hôm qua nếu lỡ nhịp

thì phải đưa rule đó vào:
- script
- cron payload
- file luật này
- hoặc memory phù hợp

---

## Production defaults hiện tại

- Timezone: `Asia/Bangkok`
- Runtime date/time: dynamic
- VN pipeline: dynamic RSS sourcing + anti-repeat
- World pipeline: dynamic runtime path, cần tiếp tục cải thiện editorial/source robustness khi cần

## Ghi nhớ thực tế rút ra

Các lỗi đã từng gặp và không được lặp lại:
- fix cứng ngày giờ trong tên file/script
- báo upload thành công nhưng Drive không có
- dùng lại story cũ vì script snapshot
- focus chưa đủ mạnh nên lineup lệch chủ đề
- xử lý asset cho toàn bộ candidate pool thay vì 5 story cuối

Nếu có mâu thuẫn giữa “độ đa dạng” và “ý người dùng đang yêu cầu rõ chủ đề”, ưu tiên **ý người dùng** trong run tay đó.
