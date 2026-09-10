import React, { useId, useState } from 'react';
import { Navigate, useLocation } from 'react-router-dom';

import { useAuth } from '../auth/AuthProvider';
import { describeError } from '../lib/http';
import { useAuthStore } from '../stores/authStore';
import { ThemeToggle } from './ThemeToggle';

const MIN_PASSWORD_LENGTH = 12;

export const AuthPage: React.FC = () => {
  const { signIn, signUp } = useAuth();
  const status = useAuthStore((state) => state.status);
  const location = useLocation();
  const emailId = useId();
  const passwordId = useId();

  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  if (status === 'authenticated') {
    const from = (location.state as { from?: string } | null)?.from;
    return <Navigate to={from ?? '/projects'} replace />;
  }

  const tooShort = mode === 'register' && password.length > 0 && password.length < MIN_PASSWORD_LENGTH;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setPending(true);
    try {
      if (mode === 'login') {
        await signIn(email, password);
      } else {
        await signUp(email, password);
      }
    } catch (caught) {
      setError(describeError(caught));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="absolute top-4 right-4">
        <ThemeToggle />
      </div>
      <form
        onSubmit={submit}
        className="w-full max-w-sm space-y-4 rounded-2xl border border-gray-200 bg-white/70 p-6 shadow-xl backdrop-blur-md dark:border-gray-800 dark:bg-gray-900/70"
      >
        <div>
          <h1 className="text-xl font-bold tracking-tight">
            {mode === 'login' ? 'Вход' : 'Регистрация'}
          </h1>
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
            Сессия хранится в защищённой cookie, токены в браузере не сохраняются.
          </p>
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor={emailId} className="text-xs font-medium text-gray-500 dark:text-gray-400">
            Email
          </label>
          <input
            id={emailId}
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className="rounded-xl border border-transparent bg-gray-100 px-3 py-2 text-sm transition-all outline-none focus:border-indigo-500 dark:bg-gray-800"
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
            className="rounded-xl border border-transparent bg-gray-100 px-3 py-2 text-sm transition-all outline-none focus:border-indigo-500 dark:bg-gray-800"
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
          disabled={pending}
          className="w-full rounded-xl bg-indigo-600 py-2.5 text-sm font-medium text-white shadow-lg shadow-indigo-500/20 transition-all hover:bg-indigo-500 disabled:opacity-60"
        >
          {pending ? 'Отправляем…' : mode === 'login' ? 'Войти' : 'Создать аккаунт'}
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
    </div>
  );
};
