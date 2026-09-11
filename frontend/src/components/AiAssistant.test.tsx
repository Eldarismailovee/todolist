import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AiAssistant } from './AiAssistant';

const assist = vi.hoisted(() => vi.fn());
vi.mock('../api/queries', () => ({ assist }));

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

describe('AiAssistant', () => {
  beforeEach(() => {
    assist.mockReset();
  });

  it('не даёт запускать ассистента без текста', () => {
    render(<AiAssistant source="   " />, { wrapper });

    expect(screen.getByRole('button', { name: 'Разбить на шаги' })).toBeDisabled();
    expect(screen.getByText(/чтобы ассистенту было с чем работать/)).toBeInTheDocument();
  });

  it('показывает подсказки и помечает демо-режим без ключа', async () => {
    assist.mockResolvedValue({
      text: 'Шаг 1\nШаг 2',
      items: ['Шаг 1', 'Шаг 2'],
      provider: 'stub',
      model: null,
    });

    render(<AiAssistant source="Организовать переезд" />, { wrapper });
    await userEvent.click(screen.getByRole('button', { name: 'Разбить на шаги' }));

    await waitFor(() => expect(screen.getByText('Шаг 1')).toBeInTheDocument());
    expect(assist).toHaveBeenCalledWith('decompose', 'Организовать переезд');
    // Ответ заглушки не должен выглядеть как ответ модели.
    expect(screen.getByText(/демо-режим без ключа/)).toBeInTheDocument();
  });

  it('передаёт выбранный пункт наружу', async () => {
    assist.mockResolvedValue({
      text: 'Купить коробки',
      items: ['Купить коробки'],
      provider: 'anthropic',
      model: 'claude-opus-5',
    });
    const onApply = vi.fn();

    render(<AiAssistant source="Переезд" onApply={onApply} applyLabel="Создать" />, { wrapper });
    await userEvent.click(screen.getByRole('button', { name: 'Предложить идеи' }));
    await waitFor(() => expect(screen.getByText('Купить коробки')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: 'Создать' }));

    expect(onApply).toHaveBeenCalledWith('Купить коробки');
    expect(screen.queryByText(/демо-режим/)).not.toBeInTheDocument();
  });

  it('показывает ошибку, а не пустой список', async () => {
    assist.mockRejectedValue(new Error('Ассистент временно недоступен'));

    render(<AiAssistant source="Переезд" />, { wrapper });
    await userEvent.click(screen.getByRole('button', { name: 'Кратко' }));

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('Ассистент временно недоступен'),
    );
  });
});
