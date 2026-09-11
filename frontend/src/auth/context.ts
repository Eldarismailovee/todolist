import { createContext, useContext } from 'react';

import type { OtpPurpose } from '../api/types';

export interface PendingOtp {
  email: string;
  purpose: OtpPurpose;
  expiresIn: number;
}

export interface AuthContextValue {
  /** Возвращает шаг подтверждения: пароль сессию ещё не создаёт. */
  startSignIn: (email: string, password: string) => Promise<PendingOtp>;
  startSignUp: (email: string, password: string) => Promise<PendingOtp>;
  confirmOtp: (pending: PendingOtp, code: string) => Promise<void>;
  signOut: () => Promise<void>;
  /** Сессия закончилась не по инициативе пользователя (401, auth_revoked). */
  requireAuthentication: () => void;
  expired: boolean;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Контекст и хук вынесены из файла компонента: иначе fast refresh
 * перезагружает модуль целиком и роняет состояние при правках.
 */
export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (context === null) throw new Error('useAuth вне AuthProvider');
  return context;
}
