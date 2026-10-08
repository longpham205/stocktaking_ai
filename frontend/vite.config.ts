import { fileURLToPath } from 'node:url';
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import type { ProxyOptions } from 'vite';
import { defineConfig } from 'vitest/config';

// The API has no CORS middleware by design: the SPA reaches it same-origin, through this proxy in
// dev and through nginx.conf once built. In docker compose the target is the `api` service.
const apiProxy: ProxyOptions = {
  target: process.env.API_PROXY_TARGET || 'http://127.0.0.1:8000',
  changeOrigin: true,
};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: { '/api': apiProxy },
    // a tunnel's public host name (scripts/run_real.sh tunnel); unset = Vite's default host check
    allowedHosts: process.env.VITE_ALLOWED_HOSTS?.split(','),
    // in docker compose on Windows/macOS, file events do not cross the bind mount: poll instead
    watch: process.env.VITE_USE_POLLING ? { usePolling: true, interval: 300 } : undefined,
  },
  preview: {
    port: 4173,
    proxy: { '/api': apiProxy },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: true,
    exclude: ['node_modules', 'dist'],
  },
});
