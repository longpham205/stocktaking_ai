# Hướng dẫn cho dev — chạy thử đầu-cuối (e2e)

Đi từ máy chưa có gì đến một giao dịch hoàn chỉnh trên trình duyệt, rồi các việc dev hay phải làm: tạo / đổi mật khẩu, đổi chế độ nhận diện, dựng lại dữ liệu, chạy test. Giải thích kiến trúc ở [`system-architecture.md`](system-architecture.md); mô tả đầy đủ từng màn hình ở [`WEB.md`](WEB.md).

Mọi lệnh `make` chạy ở **gốc repo**. Trên Windows dùng **Git Bash** (công thức trong `Makefile` là shell POSIX; PowerShell và cmd không chạy được).

## Quên mật khẩu? Chép và chạy

Các lệnh dưới chạy **trong container**, nên dùng được ở mọi terminal (PowerShell, cmd, Git Bash), miễn là đang đứng ở gốc repo và các dịch vụ đang chạy (`docker compose ps`). Mật khẩu mới in ra **một lần**: chép ngay.

Xem đang có những tài khoản nào:

```bash
docker compose exec postgres psql -U stocktaking -d stocktaking -c "select username, role, is_active from users order by id"
```

Mật khẩu mới cho `admin` (tạo tài khoản nếu chưa có):

```bash
docker compose exec api python -m entrypoints.reset_password admin --create --role admin
```

Mật khẩu mới cho một thu ngân, ví dụ `staff` (tạo nếu chưa có):

```bash
docker compose exec api python -m entrypoints.reset_password staff --create --role staff
```

Tự gõ mật khẩu thay vì để máy sinh (tối thiểu 8 ký tự, không hiện khi gõ):

```bash
docker compose exec api python -m entrypoints.reset_password admin --prompt
```

Tài khoản bị khoá (admin khoá trong màn Nhân viên): mở lại và đặt mật khẩu mới.

```bash
docker compose exec api python -m entrypoints.reset_password staff --unlock
```

Mật khẩu **nâng cao** (áp dụng thiết lập pipeline, kiểm định), khác mật khẩu đăng nhập:

```bash
docker compose exec api python -m entrypoints.reset_password --advanced
```

Nhớ: đặt lại mật khẩu sẽ đăng xuất tài khoản đó ở mọi thiết bị (đơn đang dở không mất). Bị báo "sai quá nhiều lần" thì đợi 5 phút hoặc `docker compose restart api`. Chi tiết ở mục 4.

## 0. Cách nhanh nhất: một cú nhấp

- **Windows:** nhấp đúp `run_e2e.bat` ở gốc repo (cần Git for Windows; file `.bat` tự tìm Git Bash).
- **Linux / macOS / Git Bash:** `./run_e2e.sh`

Script làm lần lượt mục 2 và mục 5 của tài liệu này, dừng ở bước đầu tiên bị lỗi:

1. kiểm công cụ (`docker`, `uv`, `make`, `openssl`), Docker đang chạy, có `backend/data_demo/`;
2. `make setup`;
3. `make docker-up` với `RECOGNIZER=fake` và `PIPELINE_CONFIG=configs/config.demo.yaml`;
4. `make seed-demo`;
5. tạo (hoặc đặt lại mật khẩu) hai tài khoản thử `e2e_admin`, `e2e_staff`;
6. `make check-env`;
7. `make smoke` → in `E2E PASSED` rồi mở `http://localhost:5173`.

Điều cần biết:

- Lần đầu mất vài phút (build image); các lần sau khoảng một phút.
- Mật khẩu của `e2e_admin` / `e2e_staff` sinh ngẫu nhiên mỗi lần chạy, chỉ dùng cho smoke và **không in ra**. Tài khoản của bạn không bị đụng. Để tự đăng nhập xem giao diện, tạo tài khoản riêng một lần: `make reset-password USER_NAME=admin ROLE=admin` (mục 4).
- Mỗi lần chạy, smoke **thanh toán một đơn thật**: đơn đó ở lại trong database và báo cáo ngày.
- `run_e2e.bat full` (hoặc `./run_e2e.sh full`): chạy thêm `make lint`, `make type-check`, `make test`, `make test-web` trước khi bật dịch vụ.
- Đổi chế độ: đặt biến trước khi gọi, ví dụ `RECOGNIZER=local ./run_e2e.sh`. `NO_OPEN=1` để không mở trình duyệt.
- Script chỉ gọi các target `make` có sẵn; khi một bước lỗi, chạy tay đúng lệnh đó theo các mục dưới để xem kỹ.

## 1. Chuẩn bị máy (một lần)

| Cần | Kiểm bằng | Ghi chú |
|---|---|---|
| Docker Desktop đang chạy | `docker compose version` | |
| `uv` | `uv --version` | https://docs.astral.sh/uv/ ; tự cài Python 3.12 |
| `make`, `openssl` | `make --version` | Git Bash có sẵn `openssl`; `make` phải cài thêm |
| `pnpm` | `pnpm --version` | chỉ cần cho `make test-web` và `make type-check` |
| Dữ liệu demo `backend/data_demo/` | `ls backend/data_demo/seed` | không nằm trong git: chép từ máy đã có, hoặc sinh theo `WEB.md` mục 3 |

`make setup` kiểm ba công cụ đầu và báo cái nào thiếu.

## 2. Chạy lần đầu

```bash
make setup
```

Tạo `.env` từ `.env.example` và sinh `JWT_SECRET`, `MEDIA_URL_SECRET`. Chạy lại không sao: bí mật đã có thì giữ nguyên.

```bash
make docker-up
```

Build image, bật `postgres`, chạy migration, bật `api` (cổng 8000) và `web` (cổng 5173). Lần đầu mất vài phút. Lệnh chỉ trả về khi các dịch vụ đã sẵn sàng.

```bash
make seed-demo
```

Nạp catalog demo (50 SKU) và giá demo (42 SKU có giá, 8 SKU cố ý để trống). Chạy lại không sinh trùng và không ghi đè giá đã sửa.

Tạo hai tài khoản và mật khẩu nâng cao (xem mục 4 để hiểu từng loại):

```bash
make reset-password USER_NAME=admin ROLE=admin
```

```bash
make reset-password USER_NAME=staff ROLE=staff
```

```bash
make reset-advanced-password
```

Mỗi lệnh in mật khẩu **một lần duy nhất**. Chép ngay vào trình quản lý mật khẩu; không có cách xem lại.

```bash
make check-env
```

Phải không có dòng `FAIL`. Dòng `WARN` về thư mục ảnh chụp chưa có là bình thường trước lượt chụp đầu tiên.

Catalog demo cần đi với config demo, nếu không ảnh gallery ở màn Sản phẩm sẽ không hiện:

```bash
PIPELINE_CONFIG=configs/config.demo.yaml docker compose up -d --wait api
```

Muốn khỏi gõ mỗi lần: đặt `PIPELINE_CONFIG=configs/config.demo.yaml` trong `.env`.

## 3. Đi một lượt e2e bằng tay

Mở `http://localhost:5173`. Dùng **hai cửa sổ khác phiên** (ví dụ một cửa sổ thường và một cửa sổ ẩn danh): một cho `staff`, một cho `admin`.

**Thu ngân (`staff`)**
1. Đăng nhập. Lần đầu sẽ thấy màn hướng dẫn, bấm qua.
2. Màn chụp: trên máy tính không có camera thì chọn ảnh từ máy. Ảnh mẫu: `backend/data_demo/query/query_01.jpg` … `query_06.jpg`.
3. Chờ nhận diện xong → màn hoá đơn. Kiểm: có ảnh kèm khung, có dòng hàng; dòng viền vàng là dòng cần xác nhận.
4. Chạm một dòng viền vàng → xác nhận hoặc đổi sang sản phẩm khác.
5. Nếu có dòng thiếu giá: nhập giá tay. Thử bấm thanh toán trước khi nhập để thấy bị chặn.
6. **Thêm món**: tìm theo tên không dấu. **Chụp thêm**: chọn ảnh thứ hai, số lượng cộng dồn.
7. Tải lại trang (F5): đơn đang dở phải còn nguyên.
8. Thanh toán tiền mặt, nhập số tiền khách đưa → màn hoàn tất, có tiền thừa.
9. **Lịch sử**: thấy đơn vừa bán.

**Quản trị (`admin`)**
1. **Báo cáo**: doanh thu hôm nay có đơn vừa bán.
2. **Đơn hàng**: thấy đơn của `staff`.
3. **Sản phẩm**: sửa giá một SKU → mở lịch sử thay đổi → hoàn tác. Mở ảnh gallery và bằng chứng nhận diện của một SKU.
4. **Sản phẩm → thử bằng chứng**: chọn một ảnh mẫu, xem kết quả OCR / màu / mã vạch.
5. **Cài đặt**: bật/tắt "cho phép thanh toán khi thiếu giá", quay lại cửa sổ `staff` kiểm hành vi đổi theo.
6. **Nhân viên**: tạo một tài khoản, khoá, mở khoá, đặt lại mật khẩu.
7. **Nâng cao**: đổi một thông số "cần áp dụng", nhập mật khẩu nâng cao, áp dụng; rồi hoàn tác.

Với `RECOGNIZER=fake` (mặc định), kết quả nhận diện là giả: lấy ngẫu nhiên từ catalog, không liên quan nội dung ảnh. Đủ để thử mọi luồng giao diện, **không** nói lên độ chính xác.

## 4. Tài khoản và mật khẩu

Có ba loại bí mật, đừng nhầm:

| Loại | Dùng để | Tạo / đổi bằng |
|---|---|---|
| Mật khẩu tài khoản | đăng nhập (`admin`, `staff`, …) | `make reset-password`, hoặc Admin → Nhân viên |
| Mật khẩu nâng cao | áp dụng thiết lập pipeline, chạy kiểm định, hoàn tác thiết lập pipeline | chỉ `make reset-advanced-password` |
| Bí mật trong `.env` (`JWT_SECRET`, `MEDIA_URL_SECRET`) | ký token đăng nhập và URL ảnh | `make setup` |

**Không có tài khoản hay mật khẩu mặc định.** Database mới hoàn toàn trống tài khoản cho tới khi bạn chạy `make reset-password`.

### Tạo và đổi mật khẩu từ dòng lệnh

```bash
make reset-password USER_NAME=an ROLE=staff
```

- Có `ROLE`: tạo tài khoản nếu chưa có; nếu đã có thì chỉ đổi mật khẩu (vai trò không đổi).
- Không có `ROLE`: chỉ đổi mật khẩu của tài khoản đã có; tài khoản chưa có thì báo lỗi.
- Mật khẩu sinh ngẫu nhiên, in một lần. Đây cũng là cách cứu khi **quên mật khẩu admin**.

Cần tự đặt mật khẩu, mở khoá tài khoản, hoặc không có `uv` trên máy: gọi thẳng lệnh trong container.

```bash
docker compose exec api python -m entrypoints.reset_password an --prompt
```

```bash
docker compose exec api python -m entrypoints.reset_password an --unlock
```

`--prompt` hỏi mật khẩu (không hiện khi gõ, tối thiểu 8 ký tự); `--unlock` mở lại tài khoản đang bị khoá, đồng thời đặt mật khẩu mới.

### Đổi từ giao diện

- Admin → **Nhân viên**: tạo tài khoản (tên 3–32 ký tự gồm chữ, số, `.` `_` `-`; mật khẩu tối thiểu 8 ký tự), đặt lại mật khẩu, khoá / mở khoá.
- Admin không tự khoá được chính mình.
- **Không có màn "tự đổi mật khẩu"**: thu ngân muốn đổi thì nhờ admin đặt lại.
- Mật khẩu nâng cao không đổi được từ giao diện.

### Điều xảy ra sau khi đổi

- Đổi mật khẩu hoặc khoá một tài khoản sẽ **đóng ca đang mở** của tài khoản đó: thiết bị đang đăng nhập bị đưa về màn đăng nhập. Đơn đang dở không mất, đăng nhập lại là thấy.
- Mỗi tài khoản chỉ có **một ca mở**: đăng nhập ở máy thứ hai sẽ đá máy thứ nhất. Khi thử e2e đừng dùng chung một tài khoản ở hai cửa sổ.
- Sai mật khẩu 5 lần liên tiếp → khoá tạm 5 phút (`LOGIN_MAX_FAILED_ATTEMPTS`, `LOGIN_LOCKOUT_MINUTES` trong `.env`). Bộ đếm nằm trong bộ nhớ: `docker compose restart api` là hết. Mật khẩu nâng cao có bộ đếm riêng, cùng giới hạn.
- Token đăng nhập hết hạn sau 8 giờ (`TOKEN_TTL_MINUTES`).
- Đổi `JWT_SECRET` → mọi người phải đăng nhập lại. Đổi `MEDIA_URL_SECRET` → các ảnh đang hiển thị hết hiệu lực cho tới khi tải lại trang.

### Giữ mật khẩu an toàn

- Không ghi mật khẩu vào repo, tài liệu, tin nhắn nhóm, hay file trong thư mục dự án. `.env` đã được gitignore: đừng `git add -f`.
- Mật khẩu in ra terminal còn nằm trong lịch sử cuộn: xoá màn hình sau khi chép.
- Sau buổi demo qua tunnel công khai, đặt lại mật khẩu các tài khoản đã dùng.

## 5. Chạy e2e tự động

Nhập hai mật khẩu vào biến của phiên shell (không hiện khi gõ, không vào lịch sử lệnh):

```bash
read -rs -p "Mat khau admin: " SMOKE_ADMIN_PW && export SMOKE_ADMIN_PW
```

```bash
read -rs -p "Mat khau staff: " SMOKE_STAFF_PW && export SMOKE_STAFF_PW
```

```bash
SMOKE_ADMIN=admin SMOKE_STAFF=staff make smoke
```

Đi 18 bước qua HTTP thật trên API đang chạy: đăng nhập, gửi ảnh, chờ job, kiểm khung và ảnh có ký, sửa dòng, thanh toán, lịch sử, báo cáo, thử bằng chứng, thiết lập nâng cao, và kiểm `staff` không vào được trang quản trị. Kết thúc bằng `SMOKE PASSED`.

- Đừng gõ mật khẩu thẳng trên dòng lệnh (`SMOKE_ADMIN_PW=... make smoke`): nó sẽ nằm trong lịch sử shell. Đóng cửa sổ Git Bash là hai biến trên mất.
- Smoke **tạo và thanh toán một đơn thật**, đơn đó ở lại trong báo cáo ngày. Chạy trên database thử, hoặc chụp lại database trước (mục 7).
- Đổi ảnh: thêm `PHOTO=data_demo/query/query_03.jpg` (đường dẫn tính từ `backend/`). API ở địa chỉ khác: `SMOKE_API=http://127.0.0.1:8001`.

Các bộ test không cần API đang chạy:

```bash
make test
```

```bash
make test-web
```

```bash
make lint
```

```bash
make type-check
```

`make test` tự bật Postgres và dùng database riêng `stocktaking_test`, không đụng dữ liệu dev.

## 6. Đổi chế độ nhận diện

| Muốn | Làm |
|---|---|
| Thử giao diện, không cần model (mặc định) | `RECOGNIZER=fake` (bản Docker: `make docker-up`) |
| **Nhận diện thật trên GPU của máy** | nhấp đúp `run_real.bat` (hoặc `./run_real.sh`) |
| Pipeline thật trong Docker | `make docker-up-gpu` — **đang hỏng, đừng chạy** (xem dưới) |

### Nhận diện thật: `run_real.bat`

Chạy API ngay trên máy bằng một môi trường Python có sẵn model, Postgres vẫn trong Docker, giao diện là Vite trên máy. Script làm lần lượt: kiểm Python (torch thấy GPU + đủ thư viện web) → bật Postgres, dừng `api` và `web` của Docker (trùng cổng) → chạy migration → bật API với `RECOGNIZER=local`, `PIPELINE_CONFIG=configs/config.yaml` và chờ nạp model (1–2 phút) → bật giao diện, mở `http://localhost:5173`.

- **Giữ cửa sổ mở** trong lúc dùng. Ctrl+C dừng cả API lẫn giao diện (Postgres vẫn chạy). Quay về bản Docker với bộ nhận diện giả: `docker compose up -d --wait api web`.
- **Python nào:** biến `ML_PYTHON` trỏ tới `python.exe` của môi trường có model; không đặt thì script tìm `../stocktaking_ai_mini/venv` cạnh repo. Môi trường đó cần torch bản CUDA, cộng thêm `fastapi uvicorn asyncpg "psycopg[binary]" alembic pyjwt`. Thiếu gì script báo đúng tên gói.
- **Không tạo tài khoản hay dữ liệu:** dùng database đang có. Quên mật khẩu thì xem mục "Quên mật khẩu", nhưng lúc này container `api` đang dừng nên chạy lệnh bằng chính Python đó, từ thư mục `backend`: `python -m entrypoints.reset_password admin`.
- **Tốc độ đã đo trên RTX 3050 Ti 4 GB:** 12–15 giây mỗi ảnh; VRAM lên tới 3,6 / 4 GB khi đang nhận diện, nên đừng chạy thứ khác dùng GPU cùng lúc.
- Log của API: `backups/run_real_api.log`.
- **Dùng trên điện thoại:** nhấp đúp `run_real_phone.bat` (= `./run_real.sh lan`) thay cho `run_real.bat`. Trên điện thoại mở địa chỉ **Network** mà cửa sổ in ra, dạng `http://172.20.10.7:5173` — KHÔNG phải `localhost` (trên điện thoại, `localhost` là chính cái điện thoại). Điện thoại và laptop phải chung mạng (cùng Wi-Fi, hoặc laptop bắt hotspot của điện thoại). Lần đầu Windows hỏi cho Node.js qua tường lửa: bấm Allow, tích cả mạng Public nếu đang dùng hotspot. Mở bằng `http://` nên không có khung ngắm camera trong trang: bấm chụp bằng camera của máy hoặc chọn ảnh.
- Đã chạy thử: script bật được, `/api/health` trả `"recognizer":"local"`, dừng thì API tắt theo. Chưa thử nhấp đúp bằng chuột và chưa thử trên Linux/macOS.

### Docker GPU: đang hỏng

`make docker-up-gpu` đã chạy thử một lần (2026-10-05): bước cài thư viện trong image tải bộ gói CUDA nhiều lần, chết với `Bus error` sau khoảng 27 phút, làm Docker Desktop ngừng hẳn và ngốn 15 GB đĩa. Nguyên nhân chưa xác định. Đừng chạy lại cho tới khi `backend/Dockerfile` được sửa. Nếu lỡ chạy và ổ C đầy: `docker builder prune -af`, rồi nén file `docker_data.vhdx` (chạy `fstrim` trong máy ảo Docker trước, nếu không file không co).

Kiểm đang chạy chế độ nào:

```bash
curl -s http://127.0.0.1:8000/api/health
```

Trả `{"status":"ready","recognizer":"fake"}` hoặc `"local"`.

## 7. Dựng lại / sao lưu dữ liệu

```bash
make db-save NAME=truoc_khi_thu
```

Ghi `backups/truoc_khi_thu.dump` (thư mục đã gitignore). Nên làm trước khi chạy smoke hoặc thử thứ gì ghi nhiều dữ liệu.

```bash
docker compose stop api
```

```bash
make db-restore NAME=truoc_khi_thu
```

```bash
docker compose start api
```

`db-restore` **thay toàn bộ** database bằng bản sao lưu: mọi thứ sau thời điểm sao lưu mất hết, kể cả tài khoản và mật khẩu đã đổi. Lệnh này chưa từng được chạy thử: lần đầu hãy thử khi dữ liệu hiện tại không quan trọng.

Bắt đầu lại từ số không (xoá sạch database, **không hoàn tác được**):

```bash
docker compose down -v
```

rồi làm lại mục 2 từ `make docker-up`. Ảnh chụp trong `backend/data/transactions/` không nằm trong database nên không bị xoá theo; dọn bằng `make purge-media DAYS=0 DRY_RUN=1` để xem, bỏ `DRY_RUN=1` để xoá.

## 8. Khi sửa code

| Sửa gì | Thấy thay đổi bằng cách |
|---|---|
| `frontend/src/` | tự nạp lại trong trình duyệt |
| `backend/app/`, `backend/entrypoints/` | `docker compose restart api` (trên Docker Desktop, `--reload` không thấy thay đổi qua bind mount) |
| `backend/app/modules/*/models.py` | `make migration MSG="..."` rồi `make migrate`, xem lại file sinh ra trong `backend/migrations/versions/` |
| `backend/pyproject.toml`, `frontend/package.json` | `make docker-up` (build lại image) |
| `.env` | `make docker-up` (container phải được tạo lại mới nhận biến mới; `restart` không đủ) |
| `backend/engine/` | thêm cổng kiểm định của pipeline: `03_DEVELOPMENT_RULES.md` mục 19.8 |

Trước khi push: `make format`, `make lint`, `make type-check`, `make test`, `make test-web`. Quy trình git: tách nhánh từ `main` mới nhất, push **nhánh**, mở PR với nền là **`main`** (PR mở vào một nhánh khác sẽ không tới `main`).

## 9. Sự cố thường gặp

| Hiện tượng | Nguyên nhân và cách xử lý |
|---|---|
| `make: command not found`, `'test' is not recognized as an internal or external command`, hoặc lỗi cú pháp lạ | đang chạy `make` trong PowerShell/cmd → dùng Git Bash. Từ PowerShell: `& "C:\Program Files\Git\bin\bash.exe" -l` rồi `cd` lại vào repo (đừng gõ `bash` trần: thường là bash của WSL). VS Code: `Terminal: Select Default Profile` → Git Bash |
| `make` in dòng `usage: ...` rồi dừng | thiếu tham số, hoặc gõ cả dấu ngoặc vuông. Trong tài liệu, `[ROLE=admin]` nghĩa là "tuỳ chọn": gõ `ROLE=admin`, không gõ ngoặc |
| `api` không lên, log có `JWT_SECRET is not set` | chưa chạy `make setup` |
| Đăng nhập báo sai dù vừa tạo tài khoản | chép thiếu ký tự; tên tài khoản luôn là chữ thường; tạo lại bằng `make reset-password` |
| "Đăng nhập sai quá nhiều lần" | đợi 5 phút hoặc `docker compose restart api` |
| Đang dùng thì bị đưa về màn đăng nhập | cùng tài khoản vừa đăng nhập ở nơi khác, hoặc mật khẩu vừa bị đặt lại |
| Màn Sản phẩm không có ảnh gallery | `api` chạy config mặc định với catalog demo → đặt `PIPELINE_CONFIG=configs/config.demo.yaml` |
| `make seed-demo` báo thiếu file | chưa có `backend/data_demo/` |
| Áp dụng thiết lập báo "Chưa có mật khẩu nâng cao" | `make reset-advanced-password` |
| Chụp báo "Hệ thống đang kiểm định" | một lượt kiểm định đang chạy; đợi xong |
| "Máy chủ đã khởi động lại, hãy chụp lại" | `api` khởi động lại khi ảnh đang chờ nhận diện; chụp lại, đơn vẫn còn |
| Kết nối Postgres từ máy rất chậm | dùng `127.0.0.1` thay `localhost` trong `DATABASE_URL` |
| `docker build` báo "Release file is not valid yet" | đồng hồ máy lệch → đồng bộ lại giờ |
| Cổng 5173 / 8000 / 5437 bị chiếm | tắt chương trình đang giữ cổng (`netstat -ano \| grep 5173`) |

Xem log: `make logs S=api` (hoặc `S=web`, `S=postgres`).
