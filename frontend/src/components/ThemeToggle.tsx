import { useThemeStore } from '../stores/themeStore';

export const ThemeToggle = () => {
  const isDark = useThemeStore((state) => state.isDark);
  const toggleTheme = useThemeStore((state) => state.toggleTheme);

  return (
    <button
      type="button"
      onClick={toggleTheme}
      aria-pressed={isDark}
      className="rounded-xl border border-gray-200 bg-white/70 px-3 py-1.5 text-xs font-medium backdrop-blur-md transition-all hover:bg-white dark:border-gray-800 dark:bg-gray-900/70 dark:hover:bg-gray-900"
    >
      {isDark ? '☾ Тёмная' : '☀ Светлая'}
    </button>
  );
};
