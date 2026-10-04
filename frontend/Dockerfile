# Four stages: deps -> dev -> build -> prod.
#
#   target: dev   `vite dev` on the source bind-mounted from the host (docker-compose.yml): edits
#                 show up without a rebuild.
#   target: prod  `pnpm build` once, served as static files by nginx (docker-compose.prod.yml).
#
# node_modules is not bind-mounted in dev: an anonymous volume over it keeps this image's own
# (Linux) install instead of the host's.
FROM node:24-alpine AS deps

RUN corepack enable && corepack prepare pnpm@12.9.1 --activate

WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile


FROM deps AS dev

COPY . .

EXPOSE 5173
CMD ["pnpm", "dev", "--host", "0.0.0.0"]


FROM deps AS build

COPY . .
RUN pnpm build


# nginx serves the built SPA and forwards /api to the API (see nginx.conf).
FROM nginx:1.27-alpine AS prod

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
