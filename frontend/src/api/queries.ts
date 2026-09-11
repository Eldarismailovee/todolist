import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '../lib/http';
import type {
  Analytics,
  AssistAction,
  AssistResult,
  Attachment,
  BoardColumn,
  Category,
  CurrentUser,
  MetaField,
  NotificationPrefs,
  Project,
  Tag,
  Task,
} from './types';

/**
 * Ключи включают userId: при смене аккаунта кэш другого пользователя не может
 * быть показан по совпадающему id проекта.
 */
export const queryKeys = {
  me: ['me'] as const,
  projects: (userId: number) => ['projects', userId] as const,
  tasks: (userId: number, projectId: number) => ['tasks', userId, projectId] as const,
  columns: (userId: number, projectId: number) => ['columns', userId, projectId] as const,
  tags: (userId: number) => ['tags', userId] as const,
  categories: (userId: number) => ['categories', userId] as const,
  analytics: (userId: number, days: number) => ['analytics', userId, days] as const,
  notifications: (userId: number) => ['notifications', userId] as const,
  search: (userId: number, query: string) => ['search', userId, query] as const,
  attributes: ['task-attributes'] as const,
};

export interface TaskFilters {
  q?: string;
  tagIds?: number[];
  categoryId?: number | null;
  completed?: boolean | null;
}

export function useProjects(userId: number) {
  return useQuery({
    queryKey: queryKeys.projects(userId),
    queryFn: async (): Promise<Project[]> => (await api.get('/projects')).data,
  });
}

export function useColumns(userId: number, projectId: number | null) {
  return useQuery({
    queryKey: queryKeys.columns(userId, projectId ?? 0),
    enabled: projectId !== null,
    queryFn: async (): Promise<BoardColumn[]> =>
      (await api.get('/board/columns', { params: { project_id: projectId } })).data,
  });
}

export function useTasks(userId: number, projectId: number | null, filters: TaskFilters = {}) {
  return useQuery({
    // Фильтры входят в ключ: иначе на экране остался бы результат прошлого запроса.
    queryKey: [...queryKeys.tasks(userId, projectId ?? 0), filters],
    enabled: projectId !== null,
    queryFn: async (): Promise<Task[]> =>
      (
        await api.get('/tasks', {
          params: {
            project_id: projectId,
            q: filters.q || undefined,
            tag_id: filters.tagIds?.length ? filters.tagIds : undefined,
            category_id: filters.categoryId ?? undefined,
            completed: filters.completed ?? undefined,
          },
        })
      ).data,
  });
}

export function useSearch(userId: number, query: string) {
  return useQuery({
    queryKey: queryKeys.search(userId, query),
    enabled: query.trim().length > 1,
    queryFn: async (): Promise<Task[]> =>
      (await api.get('/tasks/search', { params: { q: query } })).data,
  });
}

export function useTags(userId: number) {
  return useQuery({
    queryKey: queryKeys.tags(userId),
    queryFn: async (): Promise<Tag[]> => (await api.get('/tags')).data,
    staleTime: 60_000,
  });
}

export function useCategories(userId: number) {
  return useQuery({
    queryKey: queryKeys.categories(userId),
    queryFn: async (): Promise<Category[]> => (await api.get('/categories')).data,
    staleTime: 60_000,
  });
}

export function useAttributeMeta() {
  return useQuery({
    queryKey: queryKeys.attributes,
    queryFn: async (): Promise<MetaField[]> => (await api.get('/task-attributes')).data,
    staleTime: 5 * 60_000,
  });
}

export function useAnalytics(userId: number, days: number) {
  return useQuery({
    queryKey: queryKeys.analytics(userId, days),
    queryFn: async (): Promise<Analytics> =>
      (await api.get('/analytics/summary', { params: { days } })).data,
  });
}

export function useNotificationPrefs(userId: number) {
  return useQuery({
    queryKey: queryKeys.notifications(userId),
    queryFn: async (): Promise<NotificationPrefs> =>
      (await api.get('/notifications/settings')).data,
  });
}

export async function fetchCurrentUser(): Promise<CurrentUser> {
  return (await api.get('/user/me')).data;
}

export async function uploadImage(file: File): Promise<Attachment> {
  const form = new FormData();
  form.append('file', file);
  return (await api.post('/files', form)).data;
}

export async function assist(action: AssistAction, text: string): Promise<AssistResult> {
  return (await api.post('/ai/assist', { action, text })).data;
}

// --- Мутации -------------------------------------------------------------

/** Инвалидирует всё, что зависит от задач проекта. */
function invalidateBoard(queryClient: ReturnType<typeof useQueryClient>, userId: number) {
  void queryClient.invalidateQueries({ queryKey: ['tasks', userId] });
  void queryClient.invalidateQueries({ queryKey: ['analytics', userId] });
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

export function useCreateTask(userId: number, projectId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: Record<string, unknown>): Promise<Task> =>
      (await api.post('/tasks', { project_id: projectId, ...input })).data,
    onSuccess: () => invalidateBoard(queryClient, userId),
  });
}

export function useUpdateTask(userId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { id: number; patch: Record<string, unknown> }): Promise<Task> =>
      (await api.patch(`/tasks/${input.id}`, input.patch)).data,
    onSuccess: () => invalidateBoard(queryClient, userId),
  });
}

export function useMoveTask(userId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      id: number;
      column_id: number | null;
      before_id?: number | null;
      after_id?: number | null;
    }): Promise<Task> => {
      const { id, ...body } = input;
      return (await api.post(`/tasks/${id}/move`, body)).data;
    },
    onSuccess: () => invalidateBoard(queryClient, userId),
  });
}

export function useDeleteTask(userId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (taskId: number) => {
      await api.delete(`/tasks/${taskId}`);
    },
    onSuccess: () => invalidateBoard(queryClient, userId),
  });
}

export function useCreateTag(userId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (name: string): Promise<Tag> => (await api.post('/tags', { name })).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.tags(userId) });
    },
  });
}

export function useCreateCategory(userId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (name: string): Promise<Category> =>
      (await api.post('/categories', { name })).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.categories(userId) });
    },
  });
}

export function useCreateColumn(userId: number, projectId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (title: string): Promise<BoardColumn> =>
      (await api.post('/board/columns', { project_id: projectId, title })).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.columns(userId, projectId) });
    },
  });
}

export function useUpdateColumn(userId: number, projectId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { id: number; patch: Record<string, unknown> }) =>
      (await api.patch(`/board/columns/${input.id}`, input.patch)).data as BoardColumn,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.columns(userId, projectId) });
    },
  });
}

export function useSaveNotificationPrefs(userId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (prefs: NotificationPrefs): Promise<NotificationPrefs> =>
      (await api.put('/notifications/settings', prefs)).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.notifications(userId) });
    },
  });
}
