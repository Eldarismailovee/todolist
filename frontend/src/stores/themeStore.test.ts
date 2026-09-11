import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useThemeStore } from './themeStore';

describe('themeStore', () => {
  beforeEach(() => {
    localStorage.clear();
    useThemeStore.setState({ isDark: false });
  });

  it('переключает тему и запоминает выбор', () => {
    useThemeStore.getState().toggleTheme();

    expect(useThemeStore.getState().isDark).toBe(true);
    expect(localStorage.getItem('todo-theme')).toBe('dark');
  });

  it('работает, когда хранилище недоступно', () => {
    // Приватный режим и блокировка данных сайта бросают на записи.
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('заблокировано');
    });

    expect(() => useThemeStore.getState().toggleTheme()).not.toThrow();
    expect(useThemeStore.getState().isDark).toBe(true);

    setItem.mockRestore();
  });
});
