# Phase 05 — Frontend scaffold + shared layer

**Priority:** P1 · **Status:** done (2026-10-04) · Context: design.md §3

## Các bước
1. Scaffold `frontend/` (pnpm, Vite 8, React 19, TS strict, TanStack Router plugin + `tsr.config.json`, TanStack Query, Tailwind v4, shadcn `components.json` alias `@/shared/...`, zustand, i18next, vitest + Testing Library). Chỉ lấy deps lõi (báo cáo khảo sát §10), không kéo tiptap/vega/...
2. `shared/api`: `apiFetch` (Bearer, JSON/FormData/Blob body cho ảnh, 401 → clear token), `ApiError{status, detail, code}`, `keys.ts` (`qk.orders.detail(id)`, `qk.jobs(id)`, `qk.admin.products(q)`…), `types.ts` mirror schemas backend.
3. `shared/auth`: token trong sessionStorage (giống legacy), `use-auth` (login/logout/me).
4. `shared/i18n` vi (+en nếu chốt); `errors.ts` map `code` → chuỗi (thay object `ERR` legacy).
5. `routes/__root.tsx` guard + redirect theo role; `-components/app-shell.tsx`; error/pending/not-found.
6. shadcn primitives cần: button, input, dialog, sheet, tabs, table, badge, select, switch, toast(sonner), skeleton, dropdown.
7. `Dockerfile` (deps/dev/build/prod) + `nginx.conf` (`/api/` proxy, `client_max_body_size` theo max_upload_mb, SPA fallback); service `web` trong compose.
8. `test/setup.ts`, `test-utils.tsx` (`renderWithProviders`, `stubApiRoutes`).

## Done khi
`make docker-up` → `:5173` login được bằng tài khoản seed, vào shell trống theo role; `pnpm typecheck lint test build` xanh.

## Kết quả (2026-10-04)
- `frontend/`: pnpm 12.9, Vite 8.3, React 19.2, TypeScript 6.0 strict, TanStack Router (khai báo route trong code, `src/router.tsx`) + TanStack Query, Tailwind v4, component UI kiểu shadcn (button, input, label, card, badge, skeleton, spinner — lấy từ mẫu `cs-feedback-agent-main`), `sonner`, `lucide-react`, vitest 4 + Testing Library + jsdom.
- Lớp dùng chung (`src/lib/`): `api-client.ts` (`apiFetch`: Bearer, body JSON hoặc ảnh thô (Blob), `ApiError {status, code, detail, extra}`, 401 -> xoá token), `errors.ts` (mã lỗi -> câu tiếng Việt; với lỗi kiểm dữ liệu dùng `detail` của server), `auth-token.ts` (sessionStorage + lắng nghe thay đổi), `api.ts`, `types.ts`, `query-keys.ts`, `query-client.ts`, `format.ts` (VND, ngày giờ).
- `features/auth/`: `use-auth.ts` (`meQuery`, đăng nhập, đăng xuất), `login-page.tsx` (báo lỗi rõ, khoá tạm báo số phút chờ).
- Router: `/login`; route `app` chặn khi chưa có token, đọc `/api/me` một lần cho các route con; `/` chuyển theo vai trò (staff -> `/pos`, admin -> `/admin/reports`); `/admin/*` chỉ admin (staff -> `/pos`); trang lỗi/đang tải/không tìm thấy; các màn POS, lịch sử, 6 tab admin là trang chờ (Phase 6). Token mất (đăng xuất hoặc 401) -> xoá cache, về `/login`.
- Docker: `frontend/Dockerfile` (deps/dev/build/prod), `frontend/nginx.conf` (`/api/` -> `api:8000`, `client_max_body_size 12m`, `proxy_read_timeout 16m`, SPA fallback); service `web` trong `docker-compose.yml` (dev, Vite :5173, mã nguồn bind-mount) và `docker-compose.prod.yml` (nginx, cổng 5173 -> 80).
- Makefile: `make test-web`; `make type-check` chạy thêm `tsc`; `make docker-up` in địa chỉ web.
- Test: 13 test vitest (api client 5, guard của router 5, trang đăng nhập 3). `pnpm typecheck`, `pnpm test`, `pnpm build` đạt; image prod build được.
- Chạy thật: `make docker-up` -> `http://localhost:5173` chuyển về `/login`, đăng nhập bằng tài khoản thật (`e2e_staff`) -> `/pos` với menu thu ngân; gõ `/admin/users` -> về `/pos`; đăng xuất -> `/login`, token bị xoá.

## Khác kế hoạch (người dùng chốt 2026-10-04: theo mẫu của mentor)
- Không dùng `zustand` (token: module nhỏ `auth-token.ts`), `i18next` (chỉ tiếng Việt; mã lỗi -> câu ở `errors.ts`), plugin route theo file (`tsr.config.json`, `routeTree.gen.ts`), ESLint (kiểm bằng `tsc` strict; thêm sau nếu mentor yêu cầu), CLI shadcn (component chép tay như mẫu).
- Thư mục theo mẫu: `src/lib`, `src/components`, `src/features`, `src/test` thay vì `shared/`, `app/`, `routes/` của `design.md`.
- Chỉ có các component UI Phase 5 dùng; dialog/sheet/tabs/table/select/switch thêm ở Phase 6 khi cần.
