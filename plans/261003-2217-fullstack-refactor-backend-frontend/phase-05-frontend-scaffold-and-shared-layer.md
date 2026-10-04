# Phase 05 — Frontend scaffold + shared layer

**Priority:** P1 · **Status:** pending · Context: design.md §3

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
