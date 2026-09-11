import { useQueryClient } from '@tanstack/react-query';
import React, { useCallback, useEffect, useRef, useState } from 'react';

import { fetchCurrentUser } from '../api/queries';
import * as http from '../lib/http';
import { useAuthStore } from '../stores/authStore';
import { AuthContext, type PendingOtp } from './context';

const CHANNEL = 'todo-auth';

export const AuthProvider = ({ children }: { children: React.ReactNode }) => {
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

  // Восстановление сессии при загрузке: cookie может пережить перезагрузку,
  // и возврат из OAuth приходит именно так.
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

  const startSignIn = useCallback(async (email: string, password: string) => {
    const challenge = await http.login(email, password);
    return { email, purpose: challenge.purpose, expiresIn: challenge.expires_in };
  }, []);

  const startSignUp = useCallback(async (email: string, password: string) => {
    const challenge = await http.register(email, password);
    return { email, purpose: challenge.purpose, expiresIn: challenge.expires_in };
  }, []);

  const confirmOtp = useCallback(
    async (pending: PendingOtp, code: string) => {
      await http.verifyOtp(pending.email, code, pending.purpose);
      const user = await fetchCurrentUser();
      queryClient.clear();
      setUser(user);
      setExpired(false);
    },
    [queryClient, setUser],
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
    <AuthContext.Provider
      value={{
        startSignIn,
        startSignUp,
        confirmOtp,
        signOut,
        requireAuthentication,
        expired,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};
