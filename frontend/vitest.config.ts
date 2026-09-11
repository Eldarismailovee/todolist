import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    // Без явного origin у документа opaque-происхождение, и localStorage
    // в jsdom недоступен.
    environmentOptions: { jsdom: { url: 'http://localhost:5173' } },
    globals: true,
    setupFiles: ['./vitest.setup.ts'],
    // e2e гоняет Playwright, а не Vitest: иначе они запускались бы вместе.
    include: ['src/**/*.test.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/api/schema.d.ts', 'src/**/*.test.{ts,tsx}', 'src/main.tsx'],
    },
  },
});
