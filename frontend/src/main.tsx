import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import axios from 'axios';
import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import { App } from './App';
import { AuthProvider } from './auth/AuthProvider';
import './index.css';
import { AuthenticationRequired } from './lib/http';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Фоновая сверка чинит редкий пропуск публикации без разрыва SSE.
      refetchInterval: 60_000,
      refetchOnWindowFocus: true,
      staleTime: 10_000,
      retry: (failureCount, error) => {
        // Проблему авторизации решает auth-слой, а не бесконечные повторы.
        if (error instanceof AuthenticationRequired) return false;
        if (axios.isAxiosError(error) && [401, 403, 404].includes(error.response?.status ?? 0)) {
          return false;
        }
        return failureCount < 2;
      },
    },
    mutations: {
      // Повтор мутации без Idempotency-Key запрещён: сервер создал бы дубликат.
      retry: false,
    },
  },
});

const container = document.getElementById('root');
if (!container) throw new Error('Не найден #root');

createRoot(container).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
