import { create } from 'zustand';

interface ThemeState {
  isDark: boolean;
  toggleTheme: () => void;
}

const STORAGE_KEY = 'todo-theme';

function initialIsDark(): boolean {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'dark') return true;
    if (stored === 'light') return false;
  } catch {
    // Приватный режим или заблокированное хранилище: используем системную тему.
  }
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

/** Тема — единственное глобальное состояние UI; выбранный проект живёт в URL. */
export const useThemeStore = create<ThemeState>((set) => ({
  isDark: initialIsDark(),
  toggleTheme: () =>
    set((state) => {
      const isDark = !state.isDark;
      try {
        localStorage.setItem(STORAGE_KEY, isDark ? 'dark' : 'light');
      } catch {
        // Настройка не сохранится, но переключение в текущей вкладке работает.
      }
      return { isDark };
    }),
}));
