import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '../lib/http';
import type { CurrentUser, MetaField, Project, Task } from './types';

/**
 * Ключи включают userId: при смене аккаунта кэш другого пользователя не может
 * быть показан по совпадающему id проекта.
 */
export const queryKeys = {
  me: ['me'] as const,
  projects: (userId: number) => ['projects', userId] as const,
  tasks: (userId: number, projectId: number) => ['tasks', userId, projectId] as const,
  attributes: ['task-attributes'] as const,
};

export function useProjects(userId: number) {
  return useQuery({
    queryKey: queryKeys.projects(userId),
    queryFn: async (): Promise<Project[]> => (await api.get('/projects')).data,
  });
}

export function useTasks(userId: number, projectId: number | null) {
  return useQuery({
    queryKey: queryKeys.tasks(userId, projectId ?? 0),
    enabled: projectId !== null,
    queryFn: async (): Promise<Task[]> =>
      (await api.get('/tasks', { params: { project_id: projectId } })).data,
  });
}

export function useAttributeMeta() {
  return useQuery({
    queryKey: queryKeys.attributes,
    queryFn: async (): Promise<MetaField[]> => (await api.get('/task-attributes')).data,
    staleTime: 5 * 60_000,
  });
}

export async function fetchCurrentUser(): Promise<CurrentUser> {
  return (await api.get('/user/me')).data;
}

export function useCreateProject(userId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (title: string): Promise<Project> =>
      (await api.post('/projects', { title })).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.projects(userId) });
    },
  });
}

export function useToggleTaskAttribute(userId: number, projectId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    // PATCH заменяет набор атрибутов целиком — сервер валидирует его заново.
    mutationFn: async (input: { taskId: number; attributes: Record<string, unknown> }) =>
      (await api.patch(`/tasks/${input.taskId}`, { attributes: input.attributes })).data as Task,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.tasks(userId, projectId) });
    },
  });
}

export function useDeleteTask(userId: number, projectId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (taskId: number) => {
      await api.delete(`/tasks/${taskId}`);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.tasks(userId, projectId) });
    },
  });
}
