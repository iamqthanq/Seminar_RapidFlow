# RF-01: Output Contract and Semantics Audit (Gate G0)

## 1. Mục tiêu
Tài liệu này kiểm định chi tiết giao diện dòng lệnh (CLI), định dạng kết quả thực tế, và bản chất ngữ nghĩa đầu ra (Output Semantics) của RapidFlow mã nguồn gốc. Kết luận phân định rõ ràng giữa **liệt kê đầy đủ (Full Enumeration)** và **đếm số lượng (Count-Only)** để phục vụ các cổng nghiệm thu tiếp theo (G1–G6).

---

## 2. Giao diện dòng lệnh (CLI Interface)
RapidFlow nhận tham số qua parser dòng lệnh (`utility/commandparser.cpp`, `streaming/streaming_engine.cpp` dòng 530–553):

| Tham số | Ý nghĩa | Giá trị mặc định / Quy ước |
|---|---|---|
| `-d <path>` | Đường dẫn file Data Graph ban đầu | Bắt buộc |
| `-q <path>` | Đường dẫn file Query Graph | Bắt buộc |
| `-u <path>` | Đường dẫn file Stream các cập nhật cạnh (Insertion / Deletion) | Bắt buộc |
| `-num <val>` | Số lượng embedding tối đa cần tìm cho mỗi update | `MAX` (`numeric_limits<uint64_t>::max()`). Nếu đặt số nguyên (vd: `1`), dừng update ngay khi đạt số lượng này |
| `-time_limit <val>` | Giới hạn thời gian tổng (tính bằng giây) | `3600` (1 giờ). Quá thời gian này, thuật toán dừng sớm và trả về số lượng đã tìm |
| `-s <val>` | Chiều dài bước cập nhật (`step_length`) | Mặc định `10000`, tuy nhiên trong `main()` biến này không được dùng |

---

## 3. Định dạng đầu ra thực tế (Actual Output Format)
RapidFlow xuất thông tin **duy nhất qua kênh chuẩn `stdout`**. Không tạo file kết quả trừ khi kích hoạt macro `#define MEASURE_UPDATE_COST` (xuất file `.bin` lưu runtime per update).

Khi kết thúc, RapidFlow in bảng tóm tắt thực thi (`streaming/streaming_engine.cpp:398-417`):
```text
Execution Summary:
Preprocessing time (seconds): <float>
Query time (seconds): <float>
Global view update time (seconds): <float>
Local view update time (seconds): <float>
Search time (seconds): <float>
Edge process count: <uint64>
Relevant update count: <uint64>
Search count: <uint64>
Positive update count: <uint64>
Result count: <uint64>
Partial result count (exclude results): <uint64>
Invalid partial count: <uint64>
ISO conflict count: <uint64>
SI empty count: <uint64>
LC empty count: <uint64>
```

Dòng quyết định số lượng kết quả là:
```text
Result count: <uint64>
```

---

## 4. Kiểm toán ngữ nghĩa: COUNT-ONLY vs. FULL ENUMERATION

### 4.1. Bằng chứng mã nguồn từ tác giả (Author Explicit Declaration)
Tại `streaming/streaming_config.h` (dòng 10–15):
```cpp
/**
 * Both modes does not list the results.
 * 0: Count the results.
 * 1: Enumerate the results.
 */
#define EXECUTION_MODE 0
```
Tác giả ghi nhận rõ: **"Both modes does not list the results"** (cả hai chế độ đều không liệt kê/in danh sách các embedding cụ thể).

### 4.2. Cơ chế đếm ở mức lá cây tìm kiếm (Leaf-level Counting)
Tại `streaming/search_engine.cpp` (dòng 160–169):
```cpp
} else if (next_depth == target_depth - 1) {
    result_count += local_idx_[next_depth].second;
    if (result_count >= target_number) {
        for (int x = 0; x < target_depth; ++x) {
            visited_[embedding_[order[x]]] = false;
        }
        visited_[data_edge.first] = false;
        visited_[data_edge.second] = false;
        return result_count;
    }
```
Tại tầng sâu cuối cùng của cây tìm kiếm (`target_depth - 1`), thuật toán **không duyệt qua từng ứng viên** trong danh sách ứng viên lá (`local_candidates_store_`) để gán vào vector `embedding_`, mà **cộng trực tiếp độ dài danh sách ứng viên** (`local_idx_[next_depth].second`) vào `result_count`.

### 4.3. Nhân hệ số tự đẳng cấu (Automorphism Factorization)
Tại `streaming/streaming_engine.cpp` (dòng 180–181):
```cpp
uint64_t local_result_count = sm_.search_on_reduced_query(query_graph, *orders, lvm_, gvm_);
result_count += local_result_count * automorphism_size;
```
RapidFlow khai thác tính tự đẳng cấu của đồ thị truy vấn $Q$:
- Thuật toán giảm đồ thị truy vấn và chỉ tìm kiếm các canonical matching trên đồ thị thu gọn.
- Khi có kết quả cục bộ `local_result_count`, thuật toán **nhân trực tiếp với `automorphism_size`** ($|Aut(Q)|$) thay vì sinh ra từng embedding đối xứng.

### 4.4. Đoạn mã in chi tiết bị comment trong source
Tại `streaming/search_engine.cpp` (dòng 170–200), tác giả từng thử nghiệm in danh sách các cặp đỉnh `(u, v)` bằng vòng lặp `for` nhưng đã chú thích lại (commented out) và vô hiệu hóa.

---

## 5. Kết luận kiểm toán và Ràng buộc cho Gate G1–G6

1. **Khẳng định phân loại:**
   RapidFlow nguyên bản hoạt động ở chế độ **COUNT-ONLY** (đếm số lượng embedding tăng dần theo từng cập nhật cạnh). Nó **không** xuất, không lưu và không truyền ra ngoài danh sách các mapping đỉnh cụ thể $(u \mapsto v)$.
2. **Không đánh tráo khái niệm:**
   Mọi báo cáo tốc độ/thời gian của RapidFlow trên bộ test tác giả là thời gian đếm tổng hợp (`Result count`), không phải chi phí materialization hay output formatting.
3. **Ảnh hưởng tới Cổng G1 (Correctness Verification):**
   Ở Gate G1, việc kiểm chứng tính đúng đắn với một Oracle độc lập (như NewSP hay BSM Solver) cần:
   - Hoặc so sánh tổng số kết quả đếm theo từng edge update (nếu Oracle hỗ trợ đếm).
   - Hoặc phải thiết kế phương thức xuất embedding có kiểm soát chặt chẽ để đối chiếu mapping sets, nhưng cần tách biệt rõ giữa phiên bản baseline nguyên bản và bản phục vụ kiểm định.
