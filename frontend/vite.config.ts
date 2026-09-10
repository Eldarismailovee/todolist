import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

const BACKEND = process.env.BACKEND_URL ?? 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    // SPA и API обслуживаются с одного origin: refresh cookie с
    // SameSite=Strict и точная проверка Origin работают только так.
    proxy: {
      '/api': {
        target: BACKEND,
        changeOrigin: false,
        configure: (proxy) => {
          // Иначе недоступный бэкенд приходит в браузер как text/plain 500,
          // и в интерфейсе видно только «Ошибка 500».
          proxy.on('error', (error, _request, response) => {
            const message = `Бэкенд ${BACKEND} недоступен (${error.message}). Запустите: uv run fastapi dev app/main.py`;
            console.error(`\n[proxy] ${message}\n`);
            if ('writeHead' in response && !response.headersSent) {
              response.writeHead(502, { 'Content-Type': 'application/json' });
              response.end(JSON.stringify({ detail: message }));
            }
          });
        },
      },
    },
  },
});
