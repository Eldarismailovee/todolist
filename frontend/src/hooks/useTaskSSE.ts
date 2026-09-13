import { useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { z } from 'zod';

import { AuthenticationRequired } from '../lib/http';
import { openTaskStream } from '../lib/taskStream';

const TaskEvent = z.object({
  id: z.number().int().positive(),
  project_id: z.number().int().positive(),
});

const TASK_EVENTS = ['task_created', 'task_updated', 'task_deleted'];

function pause(ms: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.resolve();
  return new Promise((resolve) => {
    const finish = () => {
      clearTimeout(timer);
      signal.removeEventListener('abort', finish);
      resolve();
    };
    const timer = setTimeout(finish, ms);
    signal.addEventListener('abort', finish, { once: true });
  });
}

/**
 * Один экземпляр на авторизованный layout, а не по одному на карточку задачи.
 * Канал уже относится ко всем данным владельца, поэтому смена проекта не
 * требует переподключения.
 */
export function useTaskSSE(userId: number | null, onAuthenticationRequired: () => void): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (userId === null) return;
    const controller = new AbortController();
    let attempt = 0;

    async function run() {
      while (!controller.signal.aborted) {
        try {
          await openTaskStream(controller.signal, (event, data) => {
            if (event === 'ready') {
              attempt = 0;
              // Подписка подтверждена Redis: восстанавливаем снимок целиком,
              // потому что Pub/Sub не поддерживает replay по Last-Event-ID.
              void queryClient.invalidateQueries({ queryKey: ['tasks', userId] });
              void queryClient.invalidateQueries({ queryKey: ['projects', userId] });
            } else if (TASK_EVENTS.includes(event)) {
              const parsed = TaskEvent.parse(JSON.parse(data));
              void queryClient.invalidateQueries({
                queryKey: ['tasks', userId, parsed.project_id],
              });
            }
          });
        } catch (error) {
          if (controller.signal.aborted) return;
          // Сессию завершает только явный отказ авторизации. Перегрузка
          // сервера и пропавшая связь — повод переподключиться, а не
          // выбрасывать пользователя из аккаунта.
          if (error instanceof AuthenticationRequired) {
            onAuthenticationRequired();
            return;
          }
          // Сбой сети, Redis или разбора: следующий проход подключится заново.
        }
        await pause(
          Math.min(30_000, 1_000 * 2 ** Math.min(attempt++, 5)) + Math.random() * 500,
          controller.signal,
        );
      }
    }

    void run();
    return () => controller.abort();
  }, [userId, queryClient, onAuthenticationRequired]);
}
