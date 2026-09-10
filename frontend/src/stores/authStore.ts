import { create } from 'zustand';

import type { CurrentUser } from '../api/types';

interface AuthState {
  user: CurrentUser | null;
  /** Проверка сессии при старте ещё не завершена. */
  status: 'unknown' | 'authenticated' | 'anonymous';
  setUser: (user: CurrentUser) => void;
  clear: () => void;
}

/**
 * Хранит только идентичность и состояние интерфейса авторизации.
 * Ни access, ни refresh token сюда не попадают: access живёт в памяти вызова,
 * refresh — исключительно в HttpOnly cookie. Persist не используется.
 */
export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  status: 'unknown',
  setUser: (user) => set({ user, status: 'authenticated' }),
  clear: () => set({ user: null, status: 'anonymous' }),
}));

export const selectUserId = (state: AuthState): number | null => state.user?.id ?? null;
