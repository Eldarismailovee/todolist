import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';

import { useAuthStore } from '../stores/authStore';

export const RequireAuth: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const status = useAuthStore((state) => state.status);
  const location = useLocation();

  if (status === 'unknown') {
    // Проверка cookie ещё идёт: показывать форму входа рано.
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-gray-500">
        Проверяем сессию…
      </div>
    );
  }

  if (status === 'anonymous') {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <>{children}</>;
};
