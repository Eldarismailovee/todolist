import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '../lib/http';
import type {
  Analytics,
  AssistAction,
  AssistResult,
  Attachment,
  BoardColumn,
  BoardColumnUpdateInput,
  Category,
  CurrentUser,
  MetaField,
  NotificationPrefs,
  Project,
  Tag,
  Task,
  TaskCreateInput,
  TaskListQuery,
  TaskMoveInput,
  TaskUpdateInput,
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
    // Параметр типа обязателен: без него data остаётся any и объявленный
    // Promise<Project[]> ничего не проверяет.
    queryFn: async (): Promise<Project[]> => (await api.get<Project[]>('/projects')).data,
  });
}

export function useColumns(userId: number, projectId: number | null) {
  return useQuery({
    queryKey: queryKeys.columns(userId, projectId ?? 0),
    enabled: projectId !== null,
    queryFn: async (): Promise<BoardColumn[]> =>
      (await api.get<BoardColumn[]>('/board/columns', { params: { project_id: projectId } })).data,
  });
}

export function useTasks(userId: number, projectId: number | null, filters: TaskFilters = {}) {
  return useQuery({
    // Фильтры входят в ключ: иначе на экране остался бы результат прошлого запроса.
    queryKey: [...queryKeys.tasks(userId, projectId ?? 0), filters],
    enabled: projectId !== null,
    queryFn: async (): Promise<Task[]> => {
      const params: TaskListQuery = {
        project_id: projectId ?? 0,
        q: filters.q || undefined,
        tag_id: filters.tagIds?.length ? filters.tagIds : undefined,
        category_id: filters.categoryId ?? undefined,
        completed: filters.completed ?? undefined,
      };
      return (await api.get<Task[]>('/tasks', { params })).data;
    },
  });
}

export function useSearch(userId: number, query: string) {
  return useQuery({
    queryKey: queryKeys.search(userId, query),
    enabled: query.trim().length > 1,
    queryFn: async (): Promise<Task[]> =>
      (await api.get<Task[]>('/tasks/search', { params: { q: query } })).data,
  });
}

export function useTags(userId: number) {
  return useQuery({
    queryKey: queryKeys.tags(userId),
    queryFn: async (): Promise<Tag[]> => (await api.get<Tag[]>('/tags')).data,
    staleTime: 60_000,
  });
}

export function useCategories(userId: number) {
  return useQuery({
    queryKey: queryKeys.categories(userId),
    queryFn: async (): Promise<Category[]> => (await api.get<Category[]>('/categories')).data,
    staleTime: 60_000,
  });
}

export function useAttributeMeta() {
  return useQuery({
    queryKey: queryKeys.attributes,
    queryFn: async (): Promise<MetaField[]> =>
      (await api.get<MetaField[]>('/task-attributes')).data,
    staleTime: 5 * 60_000,
  });
}

export function useAnalytics(userId: number, days: number) {
  return useQuery({
    queryKey: queryKeys.analytics(userId, days),
    queryFn: async (): Promise<Analytics> =>
      (await api.get<Analytics>('/analytics/summary', { params: { days } })).data,
  });
}

export function useNotificationPrefs(userId: number) {
  return useQuery({
    queryKey: queryKeys.notifications(userId),
    queryFn: async (): Promise<NotificationPrefs> =>
      (await api.get<NotificationPrefs>('/notifications/settings')).data,
  });
}

export async function fetchCurrentUser(): Promise<CurrentUser> {
  return (await api.get<CurrentUser>('/user/me')).data;
}

export async function uploadImage(file: File): Promise<Attachment> {
  const form = new FormData();
  form.append('file', file);
  return (await api.post<Attachment>('/files', form)).data;
}

export async function assist(action: AssistAction, text: string): Promise<AssistResult> {
  return (await api.post<AssistResult>('/ai/assist', { action, text })).data;
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
      (await api.post<Project>('/projects', { title })).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.projects(userId) });
    },
  });
}

export function useCreateTask(userId: number, projectId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    // project_id задаёт сам хук: он известен из выбранной доски.
    mutationFn: async (input: Omit<TaskCreateInput, 'project_id'>): Promise<Task> => {
      const body: TaskCreateInput = { ...input, project_id: projectId };
      return (await api.post<Task>('/tasks', body)).data;
    },
    onSuccess: () => invalidateBoard(queryClient, userId),
  });
}

export function useUpdateTask(userId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    /**
     * `version` — номер состояния, которое видел пользователь. Сервер отвергает
     * запись по устаревшему снимку (412), вместо того чтобы затереть правку,
     * сделанную тем временем в другой вкладке. Без версии проверки нет.
     */
    mutationFn: async (input: {
      id: number;
      patch: TaskUpdateInput;
      version?: number;
    }): Promise<Task> =>
      (
        await api.patch<Task>(`/tasks/${input.id}`, input.patch, {
          headers: input.version === undefined ? {} : { 'If-Match': String(input.version) },
        })
      ).data,
    onSuccess: () => invalidateBoard(queryClient, userId),
  });
}

export function useMoveTask(userId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: TaskMoveInput & { id: number }): Promise<Task> => {
      const { id, ...body } = input;
      return (await api.post<Task>(`/tasks/${id}/move`, body satisfies TaskMoveInput)).data;
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
    mutationFn: async (name: string): Promise<Tag> => (await api.post<Tag>('/tags', { name })).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.tags(userId) });
    },
  });
}

export function useCreateCategory(userId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (name: string): Promise<Category> =>
      (await api.post<Category>('/categories', { name })).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.categories(userId) });
    },
  });
}

export function useCreateColumn(userId: number, projectId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (title: string): Promise<BoardColumn> =>
      (await api.post<BoardColumn>('/board/columns', { project_id: projectId, title })).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.columns(userId, projectId) });
    },
  });
}

export function useUpdateColumn(userId: number, projectId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    // Утверждение as BoardColumn проверку не выполняло, а подавляло: тип
    // задаётся параметром запроса, а тело — схемой контракта.
    mutationFn: async (input: {
      id: number;
      patch: BoardColumnUpdateInput;
    }): Promise<BoardColumn> =>
      (await api.patch<BoardColumn>(`/board/columns/${input.id}`, input.patch)).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.columns(userId, projectId) });
    },
  });
}

export function useSaveNotificationPrefs(userId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (prefs: NotificationPrefs): Promise<NotificationPrefs> =>
      (await api.put<NotificationPrefs>('/notifications/settings', prefs)).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.notifications(userId) });
    },
  });
}
