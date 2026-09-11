import React, { useEffect, useId, useState } from 'react';
import { Navigate, useLocation, useSearchParams } from 'react-router-dom';

import { useAuth, type PendingOtp } from '../auth/context';
import { describeError, oauthProviders, startOAuth } from '../lib/http';
import { useAuthStore } from '../stores/authStore';
import { ThemeToggle } from './ThemeToggle';

const MIN_PASSWORD_LENGTH = 12;

const PROVIDER_LABELS: Record<string, string> = {
  google: 'Войти через Google',
  github: 'Войти через GitHub',
};

const field =
  'rounded-xl border border-transparent bg-gray-100 px-3 py-2 text-sm transition-all outline-none focus:border-indigo-500 dark:bg-gray-800';

const card =
  'w-full max-w-sm space-y-4 rounded-2xl border border-gray-200 bg-white/70 p-6 shadow-xl backdrop-blur-md dark:border-gray-800 dark:bg-gray-900/70';

export const AuthPage = () => {
  const { startSignIn, startSignUp, confirmOtp } = useAuth();
  const status = useAuthStore((state) => state.status);
  const location = useLocation();
  const [params] = useSearchParams();
  const emailId = useId();
  const passwordId = useId();
  const codeId = useId();

  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [pending, setPending] = useState<PendingOtp | null>(null);
  const [providers, setProviders] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(
    params.get('oauth_error') ? 'Не удалось войти через внешний сервис' : null,
  );
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    // Список провайдеров задаёт сервер: ненастроенный провайдер не показываем.
    oauthProviders()
      .then(setProviders)
      .catch(() => setProviders([]));
  }, []);

  if (status === 'authenticated') {
    const from = (location.state as { from?: string } | null)?.from;
    return <Navigate to={from ?? '/projects'} replace />;
  }

  const tooShort =
    mode === 'register' && password.length > 0 && password.length < MIN_PASSWORD_LENGTH;

  async function submitCredentials(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      setPending(
        mode === 'login' ? await startSignIn(email, password) : await startSignUp(email, password),
      );
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function submitCode(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!pending) return;
    setError(null);
    setBusy(true);
    try {
      await confirmOtp(pending, code);
    } catch (caught) {
      setError(describeError(caught));
      setCode('');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="absolute top-4 right-4">
        <ThemeToggle />
      </div>

      {pending ? (
        <form onSubmit={submitCode} className={card}>
          <div>
            <h1 className="text-xl font-bold tracking-tight">Код из письма</h1>
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              Отправили код на {pending.email}. Он действует{' '}
              {Math.max(1, Math.round(pending.expiresIn / 60))} мин.
            </p>
          </div>

          <div className="flex flex-col gap-1.5">
            <label
              htmlFor={codeId}
              className="text-xs font-medium text-gray-500 dark:text-gray-400"
            >
              Код подтверждения
            </label>
            <input
              id={codeId}
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={10}
              autoFocus
              value={code}
              onChange={(event) => setCode(event.target.value.replace(/\D/g, ''))}
              className={`${field} text-center text-lg tracking-[0.4em]`}
            />
          </div>

          {error && (
            <p role="alert" className="text-xs text-red-500">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={busy || code.length < 4}
            className="w-full rounded-xl bg-indigo-600 py-2.5 text-sm font-medium text-white shadow-lg shadow-indigo-500/20 transition-all hover:bg-indigo-500 disabled:opacity-60"
          >
            {busy ? 'Проверяем…' : 'Подтвердить'}
          </button>

          <button
            type="button"
            onClick={() => {
              setPending(null);
              setCode('');
              setError(null);
            }}
            className="w-full text-xs text-gray-500 underline-offset-2 hover:underline dark:text-gray-400"
          >
            Изменить адрес
          </button>
        </form>
      ) : (
        <form onSubmit={submitCredentials} className={card}>
          <div>
            <h1 className="text-xl font-bold tracking-tight">
              {mode === 'login' ? 'Вход' : 'Регистрация'}
            </h1>
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              После пароля попросим код из письма. Токены в браузере не сохраняются.
            </p>
          </div>

          {providers.length > 0 && (
            <div className="space-y-2">
              {providers.map((provider) => (
                <button
                  key={provider}
                  type="button"
                  onClick={() => startOAuth(provider)}
                  className="w-full rounded-xl border border-gray-200 py-2.5 text-sm font-medium transition-all hover:bg-gray-100 dark:border-gray-800 dark:hover:bg-gray-800"
                >
                  {PROVIDER_LABELS[provider] ?? provider}
                </button>
              ))}
              <div className="flex items-center gap-3 text-xs text-gray-400">
                <span className="h-px flex-1 bg-gray-200 dark:bg-gray-800" />
                или по паролю
                <span className="h-px flex-1 bg-gray-200 dark:bg-gray-800" />
              </div>
            </div>
          )}

          <div className="flex flex-col gap-1.5">
            <label
              htmlFor={emailId}
              className="text-xs font-medium text-gray-500 dark:text-gray-400"
            >
              Email
            </label>
            <input
              id={emailId}
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className={field}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label
              htmlFor={passwordId}
              className="text-xs font-medium text-gray-500 dark:text-gray-400"
            >
              Пароль
            </label>
            <input
              id={passwordId}
              type="password"
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              required
              minLength={mode === 'register' ? MIN_PASSWORD_LENGTH : undefined}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              aria-describedby={tooShort ? `${passwordId}-hint` : undefined}
              className={field}
            />
            {tooShort && (
              <span id={`${passwordId}-hint`} className="text-xs text-red-500">
                Не короче {MIN_PASSWORD_LENGTH} символов
              </span>
            )}
          </div>

          {error && (
            <p role="alert" className="text-xs text-red-500">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-xl bg-indigo-600 py-2.5 text-sm font-medium text-white shadow-lg shadow-indigo-500/20 transition-all hover:bg-indigo-500 disabled:opacity-60"
          >
            {busy ? 'Отправляем…' : mode === 'login' ? 'Продолжить' : 'Создать аккаунт'}
          </button>

          <button
            type="button"
            onClick={() => {
              setMode(mode === 'login' ? 'register' : 'login');
              setError(null);
            }}
            className="w-full text-xs text-gray-500 underline-offset-2 hover:underline dark:text-gray-400"
          >
            {mode === 'login' ? 'Нет аккаунта? Зарегистрироваться' : 'Уже есть аккаунт? Войти'}
          </button>
        </form>
      )}
    </div>
  );
};
