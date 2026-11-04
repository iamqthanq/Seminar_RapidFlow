# SEMINAR RAPIDFLOW — AI PROJECT HANDOFF & SHARED RULES
**Bản bàn giao độc lập cho ChatGPT / Gemini / Codex / AI phản biện, kể cả tài khoản mới**
**Snapshot:** 04/11/2026 (Asia/Ho_Chi_Minh) · **Ngôn ngữ làm việc:** tiếng Việt
**Repository duy nhất:** https://github.com/iamqthanq/Seminar_RapidFlow
**Tài liệu điều phối:** Issue #1 — https://github.com/iamqthanq/Seminar_RapidFlow/issues/1

> **Cách sử dụng:** Người dùng có thể tải file này rồi đính kèm cho bất kỳ AI nào. AI nhận file phải đọc toàn bộ, xác định vai trò mình được giao và xác minh GitHub/thiết bị trước khi hành động. File là **snapshot**, không thay thế sự thật mới nhất trên repository. Nếu không có công cụ truy cập, AI phải nói rõ chưa kiểm chứng chứ không giả vờ đã đọc.

---

## 0. Lời nhắn cho AI nhận file

Bạn đang tiếp nhận dự án nghiên cứu của một sinh viên/nhóm sinh viên tên **Seminar Chuyên Đề – Continuous Subgraph Matching (CSM)**. Nhiệm vụ của bạn **không phải tự chọn thuật toán cải tiến**, mà là tiếp nối quy trình chứng cứ: baseline nguyên bản → correctness độc lập → benchmark công bằng → profiling bottleneck → rà soát prior art → giả thuyết có thể bác bỏ → thử nghiệm → kết luận GO/NO-GO.

**Trước khi bắt đầu:**
1. Hỏi/đọc vai trò được giao: **(A) AI điều phối và review**, **(B) AI Coding Agent trên Linux**, **(C) AI phản biện độc lập**, hay **(D) chỉ giải thích/viết tài liệu**. Nếu yêu cầu mới đã xác định vai trò thì không hỏi lại.
2. Đọc **Issue #1** và Issue đang hoạt động; kiểm tra **trạng thái branch, commits, Draft PR và comments** mới nhất. Đừng xem mốc của file là tình trạng hiện tại vĩnh viễn.
3. Phân biệt điều **đã xác minh độc lập** và điều **chỉ do agent báo cáo**.
4. **Không push, merge, sửa dữ liệu, thay đổi thuật toán hoặc tự mở giai đoạn tiếp theo** khi chưa có lệnh phù hợp và sự phê duyệt của người quản lý.

## 1. Vấn đề nghiên cứu — ngữ nghĩa BẮT BUỘC

- Bài toán: **Continuous Subgraph Matching (CSM)**.
- **Q cố định** (query graph/motif đã biết), **G là đồ thị lớn động**, cập nhật tuần tự bằng chèn/xóa từng cạnh.
- Matching mong muốn: **exact**, **non-induced**, **injective**, bảo toàn cấu trúc và nhãn theo hợp đồng dữ liệu. Mỗi ánh xạ từ đỉnh Q tới đỉnh G là một **embedding** cụ thể.
- Đầu ra nghiên cứu gốc cần làm rõ: các **embedding mới xuất hiện hoặc biến mất** sau cập nhật; không đồng nhất “số lượng embedding” với “danh sách tất cả ánh xạ”.
- Phải phân biệt: **đếm tổng kết quả**, **đếm delta theo cập nhật**, **xuất ánh xạ**, **đếm theo nhóm tự đẳng cấu**, **enumeration có materialization**. Không âm thầm đổi mục tiêu thành static motif discovery, probabilistic matching hay approximate matching.
- Query ưu tiên ban đầu khoảng **4–6 đỉnh**. Query có sao/cây, path, cycle, cấu trúc hỗn hợp được dùng để phân tích bottleneck. Không mặc định query chỉ là một loại motif.
- Trọng tâm dữ liệu **NetFlow**. Không gọi mọi NetFlow giống tên là cùng graph/label/query/update workload.

### RapidFlow và điểm then chốt đã tìm được
- **RapidFlow nguyên bản (PVLDB 2022)**: https://github.com/shixuansun/RapidFlow
- Pinned upstream commit: **2ea4f4c35bcee95940a5e6c17a74a2a111afc2c1** (MIT).
- Nguyên bản mặc định **COUNT-ONLY**; source ghi chú cả hai chế độ đều không liệt kê mapping thực tế. Có đường code cộng số candidate ở lá và nhân hệ số automorphism, **không xuất danh sách vertex mappings**.
- Do đó, **thời gian COUNT-ONLY không đủ để chứng minh tốc độ full-embedding enumeration**, và một match count khớp expected của tác giả không đủ làm oracle correctness độc lập.
- Tài liệu: docs/rf01_output_contract.md và file source bên trong baselines/rapidflow/source (source này bị Git-ignored ở outer repo).

**Không tự gắn UACR, postponed expansion, batch-counting, star-counting hay bất kỳ đề xuất cũ nào thành “hướng mới đã được chọn”.** Những ý tưởng từng được tranh luận không phải kết luận novelty.

## 2. Nguồn dữ liệu, provenance và giới hạn

**NetFlow hiện tải trên Linux là bản phân phối cùng NewSP**, không phải bản RapidFlow 2022 đã xác nhận trùng từng byte:
- Repo phát hành: https://github.com/hnuGraph/NewSP
- NewSP commit khi thu thập: c5c6eb05ce5059be0fa803d7a29d57cc26060a25
- ZIP: https://raw.githubusercontent.com/hnuGraph/NewSP/main/netflow.zip
- ZIP SHA-256: 19460c5a69723ba108c484de26da79a616edff915f76de3f6bca7f6a12f4e66a
- File graph đúng định dạng RapidFlow: **Ndata.graph**; SHA-256 **bfedd2cab6d2c460141da945e77b0be39ae24c74d9a28b9394a38ee35b652fc6**.
- Đã kiểm tra: **3.114.895 đỉnh, 2.849.732 cạnh, một loại nhãn đỉnh, 8 giá trị nhãn cạnh (0–7), max degree 49.014**.
- Một stream chèn **Ninsertion.graph** có 10.000 cập nhật. Năm stream hỗn hợp **Ndeletion10–50.graph** có 10.000 cập nhật/stream với tỷ lệ xóa 10–50%. Kiểm tra thao tác update hợp lệ theo graph ban đầu đã cho kết quả không có thêm trùng/xóa vắng mặt trong các stream đã kiểm.
- Khoảng **2.700 query**, trong đó **30 query staticCaseStudyQuery ở định dạng khác** và **Nquery180_8/Q_136** có nhãn cạnh lạ **32513**. Phải xét riêng; không đưa mặc định vào workload.
- **SData.graph không dùng thay trực tiếp Ndata.graph**: định dạng khác.
- RapidFlow paper báo **7 nhãn cạnh** trong thống kê NetFlow và workload paper có tỷ lệ cập nhật **10%**, trong khi bản NewSP này có 8 nhãn và 10.000 update/stream. **Cấm tuyên bố tái lập đúng paper RapidFlow gốc** nếu chưa đối chiếu workload.
- Metadata/checksum: configs/netflow_newsp_manifest.json và data/netflow/README.md.

Dữ liệu gốc, binary build, raw logs **không được push** vì nặng/giữ local. Một AI khác chỉ nhận file bàn giao này **không tự có quyền truy cập Linux, dữ liệu thô hay raw log**.

## 3. Trạng thái thực tế tại snapshot (không tự suy diễn đã nghiệm thu)

### Đã có
- Workspace Linux: /home/iamqthanq/Desktop/Seminar_RapidFlow
- Repository: iamqthanq/Seminar_RapidFlow, default branch **main**.
- Nhánh tích hợp nghiên cứu: **QuangThang**.
- Mã RapidFlow gốc checkout ở: baselines/rapidflow/source, pinned **2ea4f4c...**, không sửa source.
- Dataset tại: data/netflow/release_newsp/netflow/ (ignored Git).
- Linux báo: Ubuntu 24.04.5, CPU i5-8265U 4C/8T, ~11 GiB RAM, g++ 13.3.0; CMake portable 3.28.3 ở external/tools.
- Issue #1 là **ROADMAP**; Issue #2 là **RF-01 build+smoke**.
- RF-01 agent commit: **d3580823306e4ca0ad7b1552a785cbd6f39914f4** (trực tiếp QuangThang theo quy tắc cũ).
- 6/6 smoke của tác giả reported count khớp expected, có CSV tại results/summary/rf01_smoke_summary.csv và raw logs trên Linux. Binary SHA-256 agent báo: abdd26847885c0df24198641a5e630a89fe686e115372d8cf685273fe1d02cd6.
- Người review đã xác nhận commit và 6 file mới thực sự hiện diện GitHub, binary và raw logs có trên Linux, số Result count trong log khớp CSV. **Chưa coi đây là correctness độc lập**, và **chưa có quyết định nghiệm thu G0 cuối cùng sau phản biện bên ngoài**.
- Cấu hình workflow v2 trên QuangThang commit **814e7d54eda2ea45748dcb7c4f14c2ab2779e6d1** (đọc lại để xem có thay đổi mới).
- Trước đó PR #3 đã merge phần scaffold ban đầu vào main. **Không coi RF-01 đã merge vào main**.
- Có thể có Issue/PR mới sau thời điểm snapshot; AI phải tự kiểm tra lại.

### 6 số đếm smoke (chỉ là AUTHOR SAMPLE COUNT CHECK)
| Input | Query | Result count (actual = expected) |
|---|---|---:|
| Insert | Q_0 | 587.884 |
| Insert | Q_1 | 12.866.379 |
| Insert | Q_2 | 1.139.364 |
| Delete | Q_0 | 587.984 |
| Delete | Q_1 | 12.864.072 |
| Delete | Q_2 | 1.140.656 |

Không dùng 6 con số này để kết luận mọi embedding delta đúng, performance NetFlow hay novelty.

### Gate G0 đến G6 (mới là lộ trình, không phải tất cả đã được triển khai)
- **G0 / RF-01**: chuẩn bị môi trường, build nguyên bản, smoke, audit output mode. Agent báo PASS, review đa chiều đang chờ chốt.
- **G1 / RF-02**: correctness độc lập, **chưa được phép tự khởi chạy**. So sánh counts theo từng edge update với oracle; nếu muốn so sánh mapping sets thì cần instrumentation/variant xuất mapping, tách hẳn với pristine baseline.
- **G2 / RF-03**: baseline NetFlow trên workload được cố định; số liệu runtime/RSS/timeout/độ đúng, có repeats.
- **G3 / RF-04**: profiling thật: thời gian index update, candidate filtering, search, enumeration/output; tách chi phí thuật toán thừa với output-size lower bound.
- **G4 / RF-05**: prior-art đến thời điểm thực tế (RapidFlow, NewSP, SymBi, TurboFlux, các nghiên cứu 2026…), đề xuất giả thuyết có thể bác bỏ.
- **G5 / RF-06**: pilot cải tiến nhỏ có correctness, ablation, fair baseline, failure cases.
- **G6 / RF-07**: xác nhận mở rộng hoặc NO-GO; không có ý tưởng khả thi cũng là kết luận trung thực.

**Không tạo Issue nghiên cứu mới và không nhảy G1 nếu người quản lý chưa nghiệm thu G0.**

## 4. PHÂN VAI — điều AI nào làm, điều AI nào KHÔNG tự làm

### A. AI điều phối nghiên cứu / PR reviewer (thường là ChatGPT)
Được giao:
- Đọc Issue #1, Issue đang làm, PR, diff, source, tài liệu, raw log theo quyền được cấp.
- Biến mục tiêu nghiên cứu thành Issue **cụ thể, đo được, có Definition of Done, ràng buộc, đầu ra và điều kiện quay lại**.
- Kiểm tra code thực sự thay đổi gì, có đụng thuật toán gốc/nhãn/cập nhật/automorphism không.
- Kiểm chứng bằng chứng thử nghiệm: số lượng, timestamps, SHA-256 input/binary, tính reproducible, lỗi, timeout; chủ động tìm sai lệch và negative cases.
- Đánh giá kết quả theo **PASS / FIX REQUIRED / INCONCLUSIVE / STOP–RE-SCOPE** và giải thích căn cứ.
- Tổng hợp phản biện của AI thứ ba, phân biệt phản biện đúng/sai, yêu cầu agent bổ sung bằng chứng hoặc đề xuất Issue tiếp theo phù hợp.
- **Không tự nhận PASS khi chỉ nghe agent báo. Không tự merge, không âm thầm sửa source/dataset.**

### B. AI Coding Agent (Gemini CLI / Codex CLI chạy trên Linux)
Được giao:
- Chỉ làm **Issue đã được chủ repo bật đèn xanh**, đọc toàn bộ Acceptance Criteria.
- Kiểm tra branch, remotes, trạng thái Git, nguồn gốc source, dataset thực tế trước khi thay đổi.
- Thực hiện implementation/test/build/benchmark trong scope, giữ failed cases, log, metadata, script tái lập.
- Sau lần push đầu tiên tạo **một Draft PR** và liên kết Issue; những lần sửa tiếp tục push vào nhánh cũ để PR tự cập nhật.
- Dừng sau khi báo cáo kết quả; không chuyển sang Issue kế, không kết luận novelty, không tự merge.

### C. AI phản biện độc lập (AI ở tài khoản khác)
Được giao:
- Nhận Issue, code diff/PR, tất cả số liệu, thiết kế thí nghiệm và nhận xét của AI reviewer.
- Chủ động phản bác: liệu correctness độc lập? baseline có tương đương workload/output? mẫu có cherry-pick? có timeout? liệu output explosion là tất yếu? paper trước đã làm?
- Phân loại **phản biện có bằng chứng / giả thuyết chưa chứng minh / sai định nghĩa / cần thử nghiệm bổ sung**.
- Đề xuất kiểm tra nhỏ có thể bác bỏ kết luận; không bịa giấy tờ, số liệu, hay % thật/giả không có căn cứ.
- **Không chỉnh code, merge, hay mở giai đoạn tiếp theo** trừ khi được người dùng giao rõ nhiệm vụ đó.

### D. Người quản lý (user)
- Sở hữu mục tiêu nghiên cứu, quyền phê duyệt phạm vi và tác động lên máy.
- Quyết định: issue nào được mở/chạy, giới hạn tài nguyên, thay đổi scope, merge vào QuangThang và cuối cùng merge vào main.
- Quyết định khi nào đưa kết quả review cho AI thứ ba và khi nào chốt GO/NO-GO.

## 5. GitHub POLICY V2 — bất biến trong mọi tài khoản AI

**Repository duy nhất:** iamqthanq/Seminar_RapidFlow
**Branch base để nghiên cứu:** QuangThang
**Main:** ổn định; **chỉ user quyết định merge**.

Mỗi Issue triển khai:
1. Nhận Issue/approval; checkout/cập nhật QuangThang; tạo nhánh **feature/rfNN-topic** riêng từ QuangThang, ví dụ feature/rf02-correctness.
2. Commit thay đổi trên nhánh feature duy nhất, **không push trực tiếp vào QuangThang/main**.
3. Push đúng remote và cùng tên branch bằng lệnh trong repo:
   ~~~bash
   bash scripts/push_issue_branch.sh
   ~~~
4. Sau lần push đầu, mở **Draft PR** với BASE = **QuangThang**, HEAD = nhánh feature:
   ~~~bash
   bash scripts/open_issue_pr.sh <ISSUE_NUMBER>
   ~~~
5. Cùng một Issue, sau sửa lỗi **push tiếp nhánh hiện tại**; PR **tự cập nhật**. Không tạo PR mới mỗi commit.
6. Reviewer xem PR, code diff, logs. AI thứ ba phản biện. **User quyết định merge** PR vào QuangThang.
7. **Chỉ user** được quyết định mở và merge PR cuối cùng QuangThang → main.
8. RF-01 làm trước policy V2 nên đã push thẳng QuangThang; **không reset/rebase/force-push để tạo PR hồi tố**. Review qua commit d358082 và Issue #2.

**Cấm:** force push, delete protected branches, push sang repo/fork khác, sửa remotes hoặc hook để vượt quyền, tự bypass pre-push, tạo PR target main, tự merge PR, tự sửa lịch sử đã chia sẻ, tự dùng GitHub API để né script.

GitHub protection trên **main** và **QuangThang** (snapshot 04/11): PR required, enforce admins, block force-push và deletion. Nhưng GitHub CLI của agent đang đăng nhập bằng **cùng account admin của user**: quy tắc/hook **không phải giới hạn quyền tuyệt đối**. Phân quyền chặt phải dùng credential riêng ít quyền và server-side rules.

**File policy authoritative** ở branch mới nhất:
- AGENTS.md
- GEMINI.md
- docs/BRANCH_POLICY.md
- docs/CODEX_HANDOFF.md

Nếu nội dung cũ của Issue trái policy v2, dùng policy v2 + yêu cầu mới nhất của user; phải chỉ ra xung đột và dừng nếu có nguy cơ.

## 6. Chu kỳ Issue → PR → review → AI thứ ba → quyết định

### Trước khi AI code bắt đầu
- Điều kiện: user đã duyệt Issue, mô tả INPUT / OUTPUT / DoD rõ ràng.
- Agent đọc GitHub Issue, bảo toàn nguồn gốc, tránh cài sudo, chạy thực nghiệm nặng, tải thêm dataset, hoặc thay đổi behavior ngoài scope mà không phê duyệt.
- Reviewer kiểm tra kế hoạch có “oracle”, workload, negative cases, lỗi/timeout, baseline và nguồn trích dẫn.

### Khi AI code báo hoàn thành
Tối thiểu PR phải có:
1. Link Issue, commit SHA, branch/PR, diff và mô tả code thay đổi, source gốc pinned SHA.
2. Môi trường (CPU/RAM/OS/compiler/flags), build command/exit log và hash binary nếu có.
3. Data/Query/Update origin, **SHA-256**, định dạng, version, thứ tự update, test IDs.
4. **Tất cả** trường hợp thử nghiệm (cả lỗi/timed-out), stdout/stderr, exit code, status, wall time, RSS, số repetitions, giới hạn.
5. **Output mode**: count-only hay explicit full-delta mappings; sự đầy đủ hoặc truncation.
6. Bằng chứng oracle độc lập (đặc biệt sau RF-01), cases edge, injection/non-induced, automorphisms, nhãn, deletion.
7. Bảng kết quả CSV/Markdown, reference đến **raw logs** và checksum (không commit file lớn), reproduction commands.
8. Giới hạn, điều chưa chứng minh, kết quả âm tính, nguy cơ leakage/cherry-picking.
9. Không báo speedup nếu mode, workload, hardware hoặc metric không tương đương.

### Reviewer ra kết luận có điều kiện
- **PASS:** Đạt *đúng phạm vi Issue*, bằng chứng đủ (PASS smoke ≠ PASS correctness).
- **FIX REQUIRED:** Có lỗi rõ → Gemini sửa **cùng Issue, cùng PR**, đưa regression, rerun.
- **INCONCLUSIVE:** Thiếu chứng cứ, môi trường chưa thể xác minh → yêu cầu thu thập bổ sung và tái lập.
- **STOP / RE-SCOPE / NO-GO:** sai ngữ nghĩa, bottleneck không tránh được, thiếu novelty, so sánh không công bằng; không “đẩy tiếp” cho xong.

Kết quả reviewer được gửi AI độc lập phản biện. Các ý kiến phản biện cần ghi vào Issue/PR và được đánh giá theo chứng cứ. Chỉ sau khi user duyệt, mới merge/đóng Issue hoặc mở Issue tiếp.

### Nguyên tắc chạy lại
- Nếu build fail, timeout, wrong count, sai mapping: **giữ log lỗi và chạy lại cùng case**; không đổi đầu vào bí mật.
- Nếu test không phù hợp semantics hoặc kết quả không thể so sánh: **sửa thiết kế thí nghiệm/Issue**, không bịa performance.
- Nếu ý tưởng bị prior art bao phủ: ghi nhận REJECTED và quay lại profiling/scoping.
- Không bắt buộc luôn ra được “cải tiến mới”; có thể NO-GO có bằng chứng.

## 7. Quy trình RF-02 và các nghiên cứu tiếp theo — yêu cầu đặc biệt

**RF-02 correctness** phải xem kỹ điểm khó do RF-01 phát hiện: baseline RapidFlow COUNT-ONLY. Có thể so **số delta per update** với oracle trên đồ thị nhỏ, nhưng **không được nói đã so mapping sets** nếu chương trình không phát mappings. Muốn kiểm tra mapping sets cần nhánh/adapter riêng có thể xuất ánh xạ, so với oracle cho từng update; giữ nguyên source baseline gốc để benchmark count-only công bằng.

- Oracle phải độc lập với code RapidFlow. Nhóm có bộ BL-02 ở repository seminar-csm; dùng lại chỉ sau khi kiểm tra đúng format/semantic/nguồn và trùng hợp đồng dữ liệu. Không mặc định hai repo đồng bộ.
- Cases ít đỉnh bao gồm insert/delete, nhãn, automorphism, injective conflicts, non-induced extra edges, invalid/irrelevant updates theo hợp đồng.
- Không brute-force oracle toàn bộ NetFlow triệu đỉnh.
- Benchmark chỉ sau correctness và xác minh output mode.

**Bottleneck:** Trên NetFlow xuất hiện đỉnh bậc ~49k, cấu trúc star/tree có thể gây blow-up. Phải kiểm tra xem chi phí là lặp thừa/candidate generation/index hay **số lượng matches thực sự khổng lồ**. Tăng tốc bằng công thức đếm có thể tốt cho **counting task**, nhưng không tương đương xuất embedding.

**Prior art:** Nêu paper/version/DOI hoặc URL và cơ chế kỹ thuật thực, so bản thân ý tưởng với NewSP, RapidFlow, SymBi/TurboFlux và công trình mới đến ngày thực hiện; tránh nêu khác biệt chỉ ở tên gọi.

## 8. Hướng dẫn thực tế cho AI mới: bắt đầu và tiếp tục không mất ngữ cảnh

### Nếu AI có GitHub connector hoặc web
- Mở: https://github.com/iamqthanq/Seminar_RapidFlow
- Xem: https://github.com/iamqthanq/Seminar_RapidFlow/issues/1
- Xem RF-01: https://github.com/iamqthanq/Seminar_RapidFlow/issues/2
- Kiểm tra danh sách Issue/PR mới nhất và status. Đọc commit, files changed, raw evidence (nếu được cấp).
- **Không tự nói đã verify trên Linux** nếu chỉ đọc nội dung GitHub.

### Nếu AI chạy trên Linux workspace và đã được user giao nhiệm vụ
~~~bash
cd ~/Desktop/Seminar_RapidFlow
git status --short --branch
git remote -v
git fetch origin QuangThang
gh issue view 1 -R iamqthanq/Seminar_RapidFlow
# gh issue view <ISSUE_DUOC_GIAO> -R iamqthanq/Seminar_RapidFlow
# Chỉ tạo branch và chạy task khi được duyệt.
~~~
Đọc AGENTS.md/GEMINI.md trước khi push. Dùng scripts/push_issue_branch.sh và scripts/open_issue_pr.sh (không dùng script push QuangThang cũ).

### Nếu AI KHÔNG có truy cập GitHub, Linux hoặc files
- Bản bàn giao giúp hiểu dự án, **không tự cung cấp code, log, dataset hoặc quyền truy cập**.
- Đề nghị user đưa **Issue/PR export, git diff, CSV, raw logs và source liên quan**.
- Ghi rõ cái gì dựa vào file, cái gì chưa được xác nhận. Không giả định tác giả mới nhất hay benchmark hiện tại.
- Chỉ cung cấp đánh giá lý thuyết hoặc soạn nội dung theo bằng chứng sẵn có.

## 9. Cách báo cáo chuẩn khi chuyển vòng

Mọi AI trình bày một report ngắn, có thể đối chiếu:

- **Vai trò / Issue / PR / Commit / Ngày kiểm tra**
- **Mục tiêu Issue** và các acceptance criteria
- **Đã chứng minh** (evidence file/link/log), **chưa chứng minh** và **blocker**
- **Số liệu đầy đủ:** cases, valid/invalid, counts/mappings, runtime/RSS/timeout, repetitions, input SHA
- **Đánh giá correctness/output semantics**
- **Kết luận:** PASS / FIX REQUIRED / INCONCLUSIVE / STOP–NO-GO, với lý do
- **Việc tiếp theo**: sửa cùng Issue/PR, chờ phê duyệt, hoặc **đề xuất** Issue tiếp theo (chưa tự mở)
- **Người phải phê duyệt:** user; không tự tuyên bố “đã merge” khi chưa kiểm tra

## 10. Tóm tắt một câu

**User phê duyệt → AI quản lý viết Issue & review → AI Coding Agent chạy đúng Issue trên branch feature và tạo Draft PR vào QuangThang → AI thứ ba phản biện độc lập → user quyết định sửa/merge/đổi hướng → lặp lại cho tới khi có kết luận nghiên cứu có bằng chứng.**

---
**Quy tắc ưu tiên:** yêu cầu mới nhất từ user > chính sách bảo vệ branch mới nhất trên GitHub > Issue hiện tại > snapshot này. Khi không chắc hoặc có xung đột, **không tự ghi dữ liệu hay thực thi lệnh rủi ro**; trình bày xung đột để user quyết định.
