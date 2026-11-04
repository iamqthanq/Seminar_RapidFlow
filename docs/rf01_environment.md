# RF-01: Environment Pinning and Build Verification (Gate G0)

## 1. Mục tiêu
Tài liệu này ghim toàn bộ thông tin môi trường, phiên bản công cụ, mã nguồn gốc và cấu hình build của RapidFlow phục vụ nghiệm thu Gate G0 theo [Issue #2](https://github.com/iamqthanq/Seminar_RapidFlow/issues/2) và roadmap tổng thể [Issue #1](https://github.com/iamqthanq/Seminar_RapidFlow/issues/1).

---

## 2. Mã nguồn RapidFlow nguyên bản (Pristine Upstream)
- **Repository nguồn:** `https://github.com/shixuansun/RapidFlow` (tác giả Shixuan Sun, VLDB 2022)
- **Bản quyền:** MIT License
- **Đường dẫn local:** `baselines/rapidflow/source/` (Git submodule/checkout độc lập, được Git-ignore ở repository ngoài)
- **Commit SHA đã ghim:** `2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1`
- **Trạng thái mã nguồn:** Pristine, hoàn toàn không chỉnh sửa bất kỳ dòng mã nào (`git -C baselines/rapidflow/source status` sạch 100%).

---

## 3. Môi trường phần cứng và hệ điều hành
- **Hệ điều hành:** Ubuntu 24.04.5 LTS (Noble Numbat)
- **Linux Kernel:** `7.0.0-42-generic` (x86_64)
- **CPU:** Intel(R) Core(TM) i5-8265U CPU @ 1.60GHz
  - Kiến trúc: x86_64
  - Số lõi / luồng: 4 cores, 8 threads (2 threads/core, 1 socket)
- **Bộ nhớ RAM:** 11 GiB vật lý (~6.8 GiB available tại thời điểm chạy thực nghiệm)
- **Swap:** 4.0 GiB (sử dụng 0B)

---

## 4. Chuỗi công cụ (Toolchain)
- **C++ Compiler:** `g++ (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0`
- **Make:** `GNU Make 4.3`
- **Python:** `Python 3.12.3`
- **Bộ đo tài nguyên:** `/usr/bin/time` (GNU time 1.9)
- **Git:** `git version 2.43.0`
- **GitHub CLI:** `gh version 2.45.0`
- **CMake:**
  - Trạng thái ban đầu: Chưa được cài đặt trong `PATH` hệ thống.
  - Xử lý theo quy định Issue #2 (*"If CMake is absent, document and request permission before system package installation"*): Để không can thiệp vào gói hệ thống (`apt-get install` đòi hỏi quyền root/sudo), chúng tôi sử dụng bản binary portable chính thức Kitware CMake `3.28.3-linux-x86_64`.
  - Vị trí cài đặt portable: `external/tools/cmake-3.28.3-linux-x86_64/bin/cmake` (nằm trong thư mục `external/` đã được Git-ignore).

---

## 5. Quy trình và tham số Build
- **Lệnh build:**
  ```bash
  export PATH="$PWD/external/tools/cmake-3.28.3-linux-x86_64/bin:$PATH"
  cd baselines/rapidflow/source
  mkdir -p build && cd build
  cmake ..
  make
  ```
- **Cờ biên dịch (từ CMakeLists.txt gốc):**
  `-std=c++14 -O3 -g -Wall -pthread -march=native`
- **Cấu hình liên kết:**
  - Thư viện chia sẻ: `libgraph.so` (tạo từ `graph/graph.cpp`)
  - Thư viện tĩnh: `libutility_core.a` (tạo từ `utility/`)
  - Thực thi: `streaming/RapidFlow.out` (liên kết với `graph` và `utility_core`)
- **Kết quả build:**
  - Mã thoát: `0` (thành công 100%)
  - Binary thực thi: `baselines/rapidflow/source/build/streaming/RapidFlow.out`
    - SHA-256: `abdd26847885c0df24198641a5e630a89fe686e115372d8cf685273fe1d02cd6`
  - Thư viện chia sẻ: `baselines/rapidflow/source/build/graph/libgraph.so`
    - SHA-256: `7d14b9eb80270c3c992ea4a08277a34edb2910a0e5b164653f3705f28ce6691c`
  - Build log: `results/raw/build/build.log`
    - SHA-256: `f2f9179d07ea795717d77510d03ede6635bc00255d5e1bd73b74d05e5baec209`

---

## 6. Ghi nhận cảnh báo khi biên dịch (Compiler Diagnostics)
Trong quá trình biên dịch với GCC 13.3.0, mã nguồn gốc phát sinh một số warning nhưng không có lỗi:
1. `[-Wclass-memaccess]`: Trong `utility/sparsepp/spp_utils.h:422`, hàm `realloc` được dùng để di chuyển struct `std::pair<const std::pair<unsigned int, unsigned int>, unsigned int>` (non-trivially copyable type trong C++ tiêu chuẩn mới).
2. `[-Wsign-compare]`: So sánh signed/unsigned giữa `int` và `uint32_t` tại `utility/graphoperations.cpp:254`, `utility/computesetintersection.cpp:469`, `streaming/search_engine.cpp:163`, `streaming/streaming_engine.cpp:259`.
3. `[-Wunused-but-set-variable]`: Biến `step_length` tại `streaming/streaming_engine.cpp:545` được khởi tạo và nhận cờ `-s` nhưng không được sử dụng tiếp trong `main()`.

Toàn bộ mã nguồn C++ thượng nguồn được giữ nguyên vẹn tuyệt đối để đảm bảo tính khách quan của baseline nguyên bản.
