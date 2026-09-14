import { useQueryClient } from '@tanstack/react-query';
import React, { useCallback, useEffect, useRef, useState } from 'react';

import { fetchCurrentUser } from '../api/queries';
import * as http from '../lib/http';
import { AuthenticationRequired } from '../lib/http';
import { useAuthStore } from '../stores/authStore';
import { AuthContext, type PendingOtp } from './context';

const CHANNEL = 'todo-auth';

export const AuthProvider = ({ children }: { children: React.ReactNode }) => {
  const queryClient = useQueryClient();
  const setUser = useAuthStore((state) => state.setUser);
  const clear = useAuthStore((state) => state.clear);
  const [expired, setExpired] = useState(false);
  const [logoutUnconfirmed, setLogoutUnconfirmed] = useState(false);
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

  // Восстановление сессии при загрузке: cookie переживает перезагрузку, и
  // возврат из OAuth приходит именно так.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const user = await fetchCurrentUser();
        if (!cancelled) setUser(user);
      } catch (error) {
        // Анонимным пользователя делает только явный 401. Недоступный сервер
        // означает «неизвестно», и стирать при этом состояние нельзя: экран
        // входа выглядел бы как завершённая сессия.
        if (!cancelled && error instanceof AuthenticationRequired) clear();
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
      // Пользователь приходит тем же ответом, что и сессия: отдельный запрос
      // /me после входа больше не нужен.
      const user = await http.verifyOtp(pending.email, code, pending.purpose);
      queryClient.clear();
      setUser(user);
      setExpired(false);
      setLogoutUnconfirmed(false);
    },
    [queryClient, setUser],
  );

  const signOut = useCallback(async () => {
    // Сначала прекращаем фоновую активность: запросы не должны идти от имени
    // сессии, которую мы сейчас отзываем.
    void queryClient.cancelQueries();
    let confirmed = true;
    try {
      await http.logout();
    } catch (error) {
      // 401 означает, что сессии и так нет — выход состоялся. Любой другой
      // отказ оставляет результат неизвестным: cookie могла уцелеть, и об
      // этом нужно сказать, а не делать вид, что выход прошёл.
      confirmed = error instanceof AuthenticationRequired;
    }
    teardown();
    setExpired(false);
    setLogoutUnconfirmed(!confirmed);
    channelRef.current?.postMessage({ type: 'signed-out' });
  }, [queryClient, teardown]);

  // Стабильная ссылка: useTaskSSE пересоздал бы подписку на каждый рендер.
  const requireAuthentication = useCallback(() => {
    teardown();
    setExpired(true);
    setLogoutUnconfirmed(false);
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
        logoutUnconfirmed,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};
