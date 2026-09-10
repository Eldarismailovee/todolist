import React from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';

import { AuthPage } from './components/AuthPage';
import { BentoTaskDashboard } from './components/BentoTaskDashboard';
import { ProjectsLayout } from './components/ProjectsLayout';
import { RequireAuth } from './components/RequireAuth';
import { useThemeStore } from './stores/themeStore';

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
          <Route path=":projectId" element={<BentoTaskDashboard />} />
        </Route>
        <Route path="*" element={<Navigate to="/projects" replace />} />
      </Routes>
    </div>
  );
};
