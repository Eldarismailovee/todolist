import React, { Suspense, lazy } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';

import { AuthPage } from './components/AuthPage';
import { ProjectBoard } from './components/ProjectBoard';
import { SettingsPanel } from './components/SettingsPanel';
import { ProjectsLayout } from './components/ProjectsLayout';
import { RequireAuth } from './components/RequireAuth';
import { useThemeStore } from './stores/themeStore';

/**
 * Recharts весит больше всего остального экрана вместе взятого и нужен только
 * на вкладке аналитики — грузим его отдельным чанком, а не на входе.
 */
const AnalyticsPanel = lazy(() =>
  import('./components/AnalyticsPanel').then((module) => ({ default: module.AnalyticsPanel })),
);

const Loading = () => <p className="text-sm text-gray-500 dark:text-gray-400">Загружаем…</p>;

export const App: React.FC = () => {
  const isDark = useThemeStore((state) => state.isDark);

  return (
    <div
      className={
        isDark
          ? // OLED-ориентированный тёмный фон: глубокий серый вместо подсветки.
            'dark min-h-full bg-gray-950 text-gray-100'
          : 'min-h-full bg-gray-50 text-gray-900'
      }
    >
      <Routes>
        <Route path="/login" element={<AuthPage />} />
        <Route path="/" element={<Navigate to="/projects" replace />} />
        <Route
          path="/projects"
          element={
            <RequireAuth>
              <ProjectsLayout />
            </RequireAuth>
          }
        >
          {/* Выбранный проект живёт в URL, а не в глобальном сторе. */}
          <Route path=":projectId" element={<ProjectBoard />} />
          <Route
            path="analytics"
            element={
              <Suspense fallback={<Loading />}>
                <AnalyticsPanel />
              </Suspense>
            }
          />
          <Route path="settings" element={<SettingsPanel />} />
        </Route>
        <Route path="*" element={<Navigate to="/projects" replace />} />
      </Routes>
    </div>
  );
};
