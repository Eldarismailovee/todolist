import { useQueryClient } from '@tanstack/react-query';
import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';

import { fetchCurrentUser } from '../api/queries';
import * as http from '../lib/http';
import { useAuthStore } from '../stores/authStore';

interface AuthContextValue {
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  /** Сессия закончилась не по инициативе пользователя (401, auth_revoked). */
  requireAuthentication: () => void;
  expired: boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const CHANNEL = 'todo-auth';

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const queryClient = useQueryClient();
  const setUser = useAuthStore((state) => state.setUser);
  const clear = useAuthStore((state) => state.clear);
  const [expired, setExpired] = useState(false);
  const channelRef = useRef<BroadcastChannel | null>(null);

  /** Локальная зачистка: отменяет запросы и очищает кэш TanStack Query. */
  const teardown = useCallback(() => {
    void queryClient.cancelQueries();
    queryClient.clear();
    clear();
  }, [queryClient, clear]);

  useEffect(() => {
    const channel = new BroadcastChannel(CHANNEL);
    channelRef.current = channel;
    channel.onmessage = (message: MessageEvent<{ type?: string }>) => {
      // Вкладка, получившая сообщение, тоже прекращает запросы: у неё уже нет
      // действующей cookie.
      if (message.data?.type === 'signed-out') {
        teardown();
        setExpired(false);
      }
    };
    return () => {
      channel.close();
      channelRef.current = null;
    };
  }, [teardown]);

  // Восстановление сессии при загрузке: cookie может пережить перезагрузку.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const user = await fetchCurrentUser();
        if (!cancelled) setUser(user);
      } catch {
        if (!cancelled) clear();
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [setUser, clear]);

  const afterCredentials = useCallback(async () => {
    const user = await fetchCurrentUser();
    queryClient.clear();
    setUser(user);
    setExpired(false);
  }, [queryClient, setUser]);

  const signIn = useCallback(
    async (email: string, password: string) => {
      await http.login(email, password);
      await afterCredentials();
    },
    [afterCredentials],
  );

  const signUp = useCallback(
    async (email: string, password: string) => {
      await http.register(email, password);
      await afterCredentials();
    },
    [afterCredentials],
  );

  const signOut = useCallback(async () => {
    // Сначала прекращаем фоновую активность: SSE и запросы не должны
    // пытаться обновить токен уже отозванной сессии.
    void queryClient.cancelQueries();
    try {
      await http.logout();
    } catch {
      // Cookie всё равно недействительна для нас — состояние чистим локально.
    }
    teardown();
    setExpired(false);
    channelRef.current?.postMessage({ type: 'signed-out' });
  }, [queryClient, teardown]);

  // Стабильная ссылка: useTaskSSE пересоздал бы подписку на каждый рендер.
  const requireAuthentication = useCallback(() => {
    teardown();
    setExpired(true);
  }, [teardown]);

  return (
    <AuthContext.Provider value={{ signIn, signUp, signOut, requireAuthentication, expired }}>
      {children}
    </AuthContext.Provider>
  );
};

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (context === null) throw new Error('useAuth вне AuthProvider');
  return context;
}
