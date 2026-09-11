import '@testing-library/jest-dom/vitest';

import { vi } from 'vitest';

// jsdom не реализует matchMedia, а тема читает системную настройку при старте.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }),
});

// В этой сборке jsdom нет localStorage (документ без обычного origin).
// Подставляем минимальную реализацию: проверяем поведение стора, а не jsdom.
if (typeof globalThis.localStorage === 'undefined') {
  class MemoryStorage implements Storage {
    private data = new Map<string, string>();

    get length(): number {
      return this.data.size;
    }

    clear(): void {
      this.data.clear();
    }

    getItem(key: string): string | null {
      return this.data.get(key) ?? null;
    }

    key(index: number): string | null {
      return [...this.data.keys()][index] ?? null;
    }

    removeItem(key: string): void {
      this.data.delete(key);
    }

    setItem(key: string, value: string): void {
      this.data.set(key, String(value));
    }
  }

  const storage = new MemoryStorage();
  Object.defineProperty(globalThis, 'localStorage', { writable: true, value: storage });
  Object.defineProperty(globalThis, 'Storage', { writable: true, value: MemoryStorage });
}

// Web Locks API в jsdom тоже нет; в тестах достаточно прямого вызова.
if (!('locks' in navigator)) {
  Object.defineProperty(navigator, 'locks', {
    writable: true,
    value: {
      request: async (_name: string, callback: () => Promise<unknown>) => callback(),
    },
  });
}
