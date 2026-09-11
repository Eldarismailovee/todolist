import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';

import { assist } from '../api/queries';
import type { AssistAction } from '../api/types';
import { describeError } from '../lib/http';

interface Props {
  /** Текст, с которым работает ассистент (заголовок и описание задачи). */
  source: string;
  /** Применить один из предложенных пунктов, например создать подзадачу. */
  onApply?: (item: string) => void;
  applyLabel?: string;
}

const ACTIONS: Array<{ action: AssistAction; label: string }> = [
  { action: 'decompose', label: 'Разбить на шаги' },
  { action: 'ideas', label: 'Предложить идеи' },
  { action: 'summarize', label: 'Кратко' },
  { action: 'rewrite', label: 'Причесать текст' },
];

export const AiAssistant = ({ source, onApply, applyLabel = 'Добавить' }: Props) => {
  const [action, setAction] = useState<AssistAction | null>(null);

  const run = useMutation({
    mutationFn: (next: AssistAction) => assist(next, source),
  });

  const disabled = source.trim().length === 0;

  return (
    <section className="space-y-3 rounded-2xl border border-gray-200 bg-white/70 p-4 backdrop-blur-md dark:border-gray-800 dark:bg-gray-900/70">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold tracking-tight">AI-помощник</h3>
        {run.data?.provider === 'stub' && (
          // Честная маркировка: без ключа ответ сгенерирован без модели.
          <span className="rounded-lg bg-amber-500/15 px-2 py-0.5 text-[11px] text-amber-700 dark:text-amber-300">
            демо-режим без ключа
          </span>
        )}
      </div>

      <div className="flex flex-wrap gap-1.5">
        {ACTIONS.map((item) => (
          <button
            key={item.action}
            type="button"
            disabled={disabled || run.isPending}
            onClick={() => {
              setAction(item.action);
              run.mutate(item.action);
            }}
            className={[
              'rounded-xl border px-2.5 py-1.5 text-xs font-medium transition-all disabled:opacity-50',
              action === item.action && run.isPending
                ? 'border-indigo-500 text-indigo-600 dark:text-indigo-300'
                : 'border-gray-200 hover:bg-gray-100 dark:border-gray-800 dark:hover:bg-gray-800',
            ].join(' ')}
          >
            {item.label}
          </button>
        ))}
      </div>

      {disabled && (
        <p className="text-xs text-gray-500 dark:text-gray-400">
          Введите заголовок или описание, чтобы ассистенту было с чем работать.
        </p>
      )}

      {run.isPending && <p className="text-xs text-gray-500">Думаем…</p>}

      {run.isError && (
        <p role="alert" className="text-xs text-red-500">
          {describeError(run.error)}
        </p>
      )}

      {run.isSuccess && (
        <ul className="space-y-1.5">
          {run.data.items.map((item, index) => (
            <li
              key={`${index}-${item.slice(0, 24)}`}
              className="flex items-start justify-between gap-2 rounded-xl bg-gray-100 px-3 py-2 text-xs dark:bg-gray-800"
            >
              <span className="min-w-0 flex-1">{item}</span>
              {onApply && (
                <button
                  type="button"
                  onClick={() => onApply(item)}
                  className="shrink-0 text-indigo-600 underline-offset-2 hover:underline dark:text-indigo-300"
                >
                  {applyLabel}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
};
