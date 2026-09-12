import { QueryClient, QueryClientProvider, useMutation } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AxiosError, AxiosHeaders } from 'axios';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { Task } from '../api/types';
import { TaskDialog } from './TaskDialog';

const updateTask = vi.hoisted(() => vi.fn());

vi.mock('../api/queries', () => ({
  useTags: () => ({ data: [] }),
  useCategories: () => ({ data: [{ id: 3, name: 'Дом' }] }),
  useAttributeMeta: () => ({
    data: [{ code: 'label', title: 'Метка', type: 'string', is_required: true }],
  }),
  useCreateTag: () => useMutation({ mutationFn: async (name: string) => ({ id: 9, name }) }),
  useUpdateTask: () => useMutation({ mutationFn: updateTask, retry: false }),
  assist: vi.fn(),
}));

// Редактор Tiptap к проверке ошибок формы отношения не имеет и в jsdom стоит
// дорого: подменяется заглушкой.
vi.mock('./RichTextEditor', () => ({
  RichTextEditor: () => <div data-testid="rich-editor" />,
}));

function validationError(detail: unknown): AxiosError {
  const error = new AxiosError('request failed');
  error.response = {
    status: 422,
    statusText: '',
    data: { detail },
    headers: new AxiosHeaders(),
    config: { headers: new AxiosHeaders() },
  };
  return error;
}

const TASK: Task = {
  id: 1,
  project_id: 1,
  title: 'Задача',
  description: null,
  content: null,
  due_at: null,
  completed_at: null,
  column_id: null,
  category_id: null,
  position: 1000,
  attributes: {},
  tags: [],
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

describe('TaskDialog: ошибки валидации', () => {
  beforeEach(() => {
    updateTask.mockReset();
  });

  it('показывает сообщение у поля, связывает его с input и ставит фокус', async () => {
    updateTask.mockRejectedValue(
      validationError([{ loc: ['body', 'title'], msg: 'Слишком длинный заголовок' }]),
    );
    const onClose = vi.fn();

    render(<TaskDialog task={TASK} userId={1} onClose={onClose} />, { wrapper });
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    const input = await screen.findByLabelText('Заголовок');
    await waitFor(() => expect(input).toHaveAttribute('aria-invalid', 'true'));
    const messageId = input.getAttribute('aria-describedby');
    expect(messageId).toBeTruthy();
    expect(document.getElementById(messageId as string)).toHaveTextContent(
      'Слишком длинный заголовок',
    );
    // Отказ сервера не должен выглядеть как успешное сохранение.
    expect(onClose).not.toHaveBeenCalled();
    expect(input).toHaveFocus();
  });

  it('показывает ошибку динамического атрибута у его поля', async () => {
    updateTask.mockRejectedValue(
      validationError([{ loc: ['body', 'attributes', 'label'], msg: 'обязательное поле' }]),
    );

    render(<TaskDialog task={TASK} userId={1} onClose={vi.fn()} />, { wrapper });
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    const input = await screen.findByLabelText(/Метка/);
    await waitFor(() => expect(input).toHaveAttribute('aria-invalid', 'true'));
    const messageId = input.getAttribute('aria-describedby');
    expect(document.getElementById(messageId as string)).toHaveTextContent('обязательное поле');
  });

  it('показывает ошибку по неизвестному форме полю общим сообщением', async () => {
    // Атрибута deadline нет в справочнике этого клиента: показать его у поля
    // негде, и сообщение не должно пропасть.
    updateTask.mockRejectedValue(
      validationError([{ loc: ['body', 'attributes', 'deadline'], msg: 'обязательное поле' }]),
    );

    render(<TaskDialog task={TASK} userId={1} onClose={vi.fn()} />, { wrapper });
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    // Путь целиком: иначе непонятно, какой именно атрибут не заполнен.
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('attributes.deadline: обязательное поле'),
    );
  });

  it('закрывает диалог только после успешного сохранения', async () => {
    updateTask.mockResolvedValue({ ...TASK });
    const onClose = vi.fn();

    render(<TaskDialog task={TASK} userId={1} onClose={onClose} />, { wrapper });
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});
