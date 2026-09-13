import React, { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate, useParams } from 'react-router-dom';

import { useCreateProject, useProjects } from '../api/queries';
import { useAuth } from '../auth/context';
import { useTaskSSE } from '../hooks/useTaskSSE';
import { describeError } from '../lib/http';
import { useAuthStore } from '../stores/authStore';
import { ThemeToggle } from './ThemeToggle';

export const ProjectsLayout = () => {
  const user = useAuthStore((state) => state.user);
  const { signOut, requireAuthentication, expired, logoutUnconfirmed } = useAuth();
  const navigate = useNavigate();
  const { projectId } = useParams();
  const [title, setTitle] = useState('');

  const userId = user?.id ?? null;

  useTaskSSE(userId, requireAuthentication);

  const projects = useProjects(userId ?? 0);
  const createProject = useCreateProject(userId ?? 0);

  const firstProjectId = projects.data?.[0]?.id;
  const location = useLocation();
  // Автопереход только с «голого» /projects: страницы аналитики и настроек
  // тоже вложены в этот layout и не должны перебиваться проектом.
  const onProjectsRoot = location.pathname.replace(/\/+$/, '') === '/projects';
  useEffect(() => {
    if (onProjectsRoot && !projectId && firstProjectId !== undefined) {
      navigate(`/projects/${firstProjectId}`, { replace: true });
    }
  }, [onProjectsRoot, projectId, firstProjectId, navigate]);

  async function addProject(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = title.trim();
    if (!trimmed) return;
    const project = await createProject.mutateAsync(trimmed);
    setTitle('');
    navigate(`/projects/${project.id}`);
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-7xl flex-col gap-6 p-4 md:p-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Задачи</h1>
          <p className="text-xs text-gray-500 dark:text-gray-400">{user?.email}</p>
        </div>
        <div className="flex items-center gap-2">
          <NavLink
            to="/projects/analytics"
            className={({ isActive }) =>
              [
                'rounded-xl border px-3 py-1.5 text-xs font-medium transition-all',
                isActive
                  ? 'border-indigo-500 text-indigo-600 dark:text-indigo-300'
                  : 'border-gray-200 hover:bg-gray-100 dark:border-gray-800 dark:hover:bg-gray-800',
              ].join(' ')
            }
          >
            Аналитика
          </NavLink>
          <NavLink
            to="/projects/settings"
            className={({ isActive }) =>
              [
                'rounded-xl border px-3 py-1.5 text-xs font-medium transition-all',
                isActive
                  ? 'border-indigo-500 text-indigo-600 dark:text-indigo-300'
                  : 'border-gray-200 hover:bg-gray-100 dark:border-gray-800 dark:hover:bg-gray-800',
              ].join(' ')
            }
          >
            Уведомления
          </NavLink>
          <ThemeToggle />
          <button
            type="button"
            onClick={() => void signOut()}
            className="rounded-xl border border-gray-200 bg-white/70 px-3 py-1.5 text-xs font-medium backdrop-blur-md transition-all hover:bg-white dark:border-gray-800 dark:bg-gray-900/70 dark:hover:bg-gray-900"
          >
            Выйти
          </button>
        </div>
      </header>

      {expired && (
        <div
          role="alert"
          className="rounded-2xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-200"
        >
          Сессия завершена. Войдите снова, чтобы продолжить.{' '}
          <button
            type="button"
            onClick={() => navigate('/login')}
            className="font-medium underline underline-offset-2"
          >
            Перейти ко входу
          </button>
        </div>
      )}

      {logoutUnconfirmed && (
        <div
          role="alert"
          className="rounded-2xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-200"
        >
          Выход не подтверждён сервером: ответа не было. На этом устройстве вы вышли, но сессия
          могла остаться активной — при первой возможности выйдите ещё раз.
        </div>
      )}

      <div className="grid gap-6 md:grid-cols-[260px_1fr]">
        <aside className="space-y-3">
          <form
            onSubmit={addProject}
            className="rounded-2xl border border-gray-200 bg-white/70 p-4 shadow-xl backdrop-blur-md dark:border-gray-800 dark:bg-gray-900/70"
          >
            <label
              htmlFor="new-project"
              className="text-xs font-medium text-gray-500 dark:text-gray-400"
            >
              Новый проект
            </label>
            <div className="mt-1.5 flex gap-2">
              <input
                id="new-project"
                value={title}
                maxLength={255}
                onChange={(event) => setTitle(event.target.value)}
                className="min-w-0 flex-1 rounded-xl border border-transparent bg-gray-100 px-3 py-2 text-sm transition-all outline-none focus:border-indigo-500 dark:bg-gray-800"
              />
              <button
                type="submit"
                disabled={createProject.isPending || title.trim().length === 0}
                className="rounded-xl bg-indigo-600 px-3 py-2 text-sm font-medium text-white transition-all hover:bg-indigo-500 disabled:opacity-60"
              >
                +
              </button>
            </div>
            {createProject.isError && (
              <p role="alert" className="mt-2 text-xs text-red-500">
                {describeError(createProject.error)}
              </p>
            )}
          </form>

          <nav className="space-y-1.5">
            {projects.isPending && <p className="px-1 text-xs text-gray-500">Загружаем проекты…</p>}
            {projects.isError && (
              <p role="alert" className="px-1 text-xs text-red-500">
                {describeError(projects.error)}
              </p>
            )}
            {projects.data?.map((project) => (
              <NavLink
                key={project.id}
                to={`/projects/${project.id}`}
                className={({ isActive }) =>
                  [
                    'block truncate rounded-xl border px-3 py-2 text-sm transition-all',
                    isActive
                      ? 'border-indigo-500/40 bg-indigo-600/10 font-medium text-indigo-700 dark:text-indigo-300'
                      : 'border-transparent hover:bg-gray-100 dark:hover:bg-gray-900',
                  ].join(' ')
                }
              >
                {project.title}
              </NavLink>
            ))}
            {projects.data?.length === 0 && (
              <p className="px-1 text-xs text-gray-500 dark:text-gray-400">
                Пока нет проектов — создайте первый.
              </p>
            )}
          </nav>
        </aside>

        {/* min-w-0: без него колонка грида растягивается содержимым
            доски и по всей странице появляется горизонтальная прокрутка. */}
        <main className="min-w-0">
          <Outlet />
        </main>
      </div>
    </div>
  );
};
