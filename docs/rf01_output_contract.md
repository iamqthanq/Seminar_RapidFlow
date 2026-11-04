# RF-01: Output Contract and Semantics Audit (Gate G0)

## 1. Mục tiêu
Tài liệu này kiểm định chi tiết giao diện dòng lệnh (CLI), định dạng kết quả thực tế, và bản chất ngữ nghĩa đầu ra (Output Semantics) của RapidFlow mã nguồn gốc. Kết luận phân định rõ ràng giữa **liệt kê đầy đủ (Full Enumeration)** và **đếm số lượng (Count-Only)**, đồng thời làm rõ ngữ nghĩa tích lũy dòng sự kiện (stream aggregation) để phục vụ các cổng nghiệm thu tiếp theo (G1–G6).

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

## 5. Phân tích chi tiết ngữ nghĩa Result count (Giải quyết R2)

Nhằm làm rõ bản chất của `Result count` phục vụ việc thiết kế kiểm chứng độc lập ở Cổng G1 (RF-02), dưới đây là phân tích chi tiết dựa trên mã nguồn thực tế:

### 5.1. Bản chất của chỉ số `Result count`
- **Là tổng cộng dồn trên toàn bộ stream (Stream-Aggregated Count):**
  Trong `streaming/streaming_engine.h` và `streaming/streaming_engine.cpp`:
  - Phép gán `result_count_ = 0;` được định nghĩa tại `streaming/streaming_engine.h:52–64` (dòng 58, bên trong phương thức `reset_performance_counters()`), và được gọi từ `streaming/streaming_engine.cpp:335` ngay trước khi bắt đầu duyệt stream.
  - Dòng 350–352 trong `streaming/streaming_engine.cpp`:
    ```cpp
    for (auto& update : stream) {
        execute(query_graph, update, true, true);
    ```
  - Dòng 240 (bên trong hàm `execute`):
    ```cpp
    result_count_ += result_count;
    ```
  - Dòng 411 (trong hàm `print_metrics`):
    ```cpp
    printf("Result count: %zu\n", result_count_);
    ```
  Như vậy, `Result count` cuối cùng là $\sum_{i=1}^{M} \Delta_i$, trong đó $\Delta_i$ là số lượng match mới xuất hiện hoặc bị hủy ở cập nhật thứ $i$ ($M = 1{,}221{,}704$ trong bộ test của tác giả).

### 5.2. Các câu hỏi trọng tâm về ngữ nghĩa
1. **`Result count` cuối chương trình là gì?**
   Là tổng số lượt match được phát hiện tích lũy qua tất cả các cập nhật cạnh trong file stream.
2. **Nó được cộng dồn qua những cập nhật nào?**
   Được cộng dồn qua **tất cả** các cập nhật cạnh hợp lệ trong file stream đầu vào (`stream_path`).
3. **Nó có phải số embedding của trạng thái graph cuối cùng không?**
   **TUYỆT ĐỐI KHÔNG.** Trong bài toán Continuous Subgraph Matching (CSM), dữ liệu là dòng sự kiện biến đổi đồ thị. Nếu một embedding xuất hiện tại bước $t_1$, bị xóa tại bước $t_2$, rồi lại xuất hiện tại bước $t_3$, thì mỗi lần xuất hiện/biến mất đều được tính vào dòng sự kiện. Chỉ số này không phản ánh số lượng subgraph isomorphism tồn tại trên snapshot đồ thị tĩnh $G_{final}$.
4. **Nó có phải delta của một update duy nhất không?**
   **KHÔNG.** Nó là tổng dồn qua toàn bộ stream. Từ một số duy nhất ở cuối chương trình, không thể biết được update nào sinh ra bao nhiêu match.
5. **Nó có cung cấp danh sách mapping không?**
   **KHÔNG.** Thuật toán chỉ đếm số lượng, không sinh các bộ ánh xạ $f: V(Q) \to V(G)$.
6. **Với phép Deletion, cần hiểu số đếm theo ngữ nghĩa nào?**
   Với phép xóa cạnh (`Update::op_ == REMOVE`), khi một cạnh $(u, v)$ bị gỡ bỏ, `search_on_reduced_query` được kích hoạt **trước khi** cập nhật các index cấu trúc để tìm tất cả các embedding đang chứa cạnh $(u, v)$ đó. Do đó, `Result count` trong kịch bản deletion là **tổng số embedding bị hủy bỏ (invalidated / removed matches)** do các cạnh bị xóa.
7. **Có thể dùng trực tiếp chỉ số này để kiểm chứng correctness theo từng update không?**
   **KHÔNG THỂ.** Để kiểm chứng tính đúng đắn độc lập ở Cổng G1 (RF-02):
   - Một chỉ số tổng hợp duy nhất cho $1.2$ triệu updates có thể che giấu các sai số bù trừ (cancellation of errors: bước $A$ thừa 2 match, bước $B$ thiếu 2 match dẫn đến tổng vẫn bằng nhau).
   - Muốn kiểm chứng correctness nghiêm ngặt, bắt buộc phải đối chiếu số đếm hoặc tập hợp kết quả theo **từng bước cập nhật đơn lẻ** (per-update delta validation).

---

## 6. Kết luận kiểm toán và Ràng buộc cho Gate G1–G6

1. **Khẳng định phân loại:**
   RapidFlow nguyên bản hoạt động ở chế độ **COUNT-ONLY STREAM-AGGREGATED**. Thuật toán không bao giờ xuất hay lưu trữ tập ánh xạ cụ thể $(u \mapsto v)$.
2. **Không đánh tráo khái niệm:**
   - RapidFlow COUNT-ONLY **không chứng minh được** khả năng liệt kê đầy đủ (Full Enumeration).
   - Tốc độ chạy của RapidFlow đạt được một phần nhờ việc cắt tỉa tại lá (cộng trực tiếp kích thước mảng ứng viên) và nhân hệ số tự đẳng cấu $|Aut(Q)|$ mà không phải duyệt từng nghiệm.
3. **Yêu cầu đối với Gate G1 (Correctness Verification):**
   Để kiểm chứng tính đúng đắn ở Gate G1 với Oracle độc lập:
   - Phải thiết kế kịch bản kiểm thử per-update (chạy từng update hoặc checkpoint từng bước).
   - Đối chiếu số đếm per-update giữa RapidFlow và Oracle.
   - Nếu muốn đối chiếu tập ánh xạ (mapping sets), phải xây dựng một adapter/wrapper hỗ trợ explicit mapping có kiểm soát chặt chẽ, tách bạch hoàn toàn với mã nguồn baseline nguyên bản.
