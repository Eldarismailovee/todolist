import { useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { useAnalytics } from '../api/queries';
import { describeError } from '../lib/http';
import { useAuthStore } from '../stores/authStore';
import { useThemeStore } from '../stores/themeStore';

/**
 * Цвета взяты из проверенной категориальной палитры (слоты 1 и 2) и
 * прогнаны валидатором на светлой и тёмной поверхности: разделение по CVD
 * и контраст с фоном проходят в обоих режимах.
 */
const SERIES = {
  light: { created: '#2a78d6', completed: '#eb6834', grid: '#e5e7eb', text: '#52514e' },
  dark: { created: '#3987e5', completed: '#d95926', grid: '#1f2937', text: '#c3c2b7' },
};

const RANGES = [
  { days: 7, label: '7 дней' },
  { days: 30, label: '30 дней' },
  { days: 90, label: '90 дней' },
];

const Tile = ({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string | number;
  hint?: string;
  tone?: 'warn';
}) => (
  <div className="rounded-2xl border border-gray-200 bg-white/70 p-5 shadow-xl backdrop-blur-md dark:border-gray-800 dark:bg-gray-900/70">
    <p className="text-xs font-medium text-gray-500 dark:text-gray-400">{label}</p>
    <p
      className={[
        'mt-1 text-3xl font-bold tracking-tight',
        tone === 'warn' ? 'text-red-600 dark:text-red-400' : '',
      ].join(' ')}
    >
      {value}
    </p>
    {hint && <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">{hint}</p>}
  </div>
);

export const AnalyticsPanel = () => {
  const user = useAuthStore((state) => state.user);
  const isDark = useThemeStore((state) => state.isDark);
  const [days, setDays] = useState(30);
  const analytics = useAnalytics(user?.id ?? 0, days);

  const palette = isDark ? SERIES.dark : SERIES.light;

  const tooltipStyle = {
    backgroundColor: isDark ? '#111827' : '#ffffff',
    border: `1px solid ${palette.grid}`,
    borderRadius: 12,
    fontSize: 12,
    color: isDark ? '#f3f4f6' : '#111827',
  };

  if (analytics.isError) {
    return (
      <p role="alert" className="text-sm text-red-500">
        {describeError(analytics.error)}
      </p>
    );
  }

  const data = analytics.data;

  return (
    <div className="space-y-6">
      {/* Фильтр периода — одной строкой над графиками. */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-bold tracking-tight">Аналитика</h2>
        <div className="flex gap-1.5">
          {RANGES.map((range) => (
            <button
              key={range.days}
              type="button"
              aria-pressed={days === range.days}
              onClick={() => setDays(range.days)}
              className={[
                'rounded-xl border px-3 py-1.5 text-xs font-medium transition-all',
                days === range.days
                  ? 'border-indigo-500 bg-indigo-600/10 text-indigo-700 dark:text-indigo-300'
                  : 'border-gray-200 hover:bg-gray-100 dark:border-gray-800 dark:hover:bg-gray-800',
              ].join(' ')}
            >
              {range.label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Tile label="Всего задач" value={data?.total ?? 0} />
        <Tile
          label="Выполнено"
          value={data?.completed ?? 0}
          hint={`${Math.round((data?.completion_rate ?? 0) * 100)}% от всех`}
        />
        <Tile
          label="Просрочено"
          value={data?.overdue ?? 0}
          tone={data && data.overdue > 0 ? 'warn' : undefined}
        />
        <Tile label="Скоро срок" value={data?.due_soon ?? 0} hint="в ближайшие 7 дней" />
      </div>

      <section className="rounded-2xl border border-gray-200 bg-white/70 p-5 shadow-xl backdrop-blur-md dark:border-gray-800 dark:bg-gray-900/70">
        <h3 className="text-sm font-semibold tracking-tight">Создано и выполнено по дням</h3>
        <div className="mt-4 h-64">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data?.daily ?? []} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
              <CartesianGrid stroke={palette.grid} strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 11, fill: palette.text }}
                tickFormatter={(value: string) => value.slice(5)}
                stroke={palette.grid}
                minTickGap={24}
              />
              <YAxis
                allowDecimals={false}
                tick={{ fontSize: 11, fill: palette.text }}
                stroke={palette.grid}
              />
              <Tooltip contentStyle={tooltipStyle} labelFormatter={(value) => `Дата: ${value}`} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line
                type="monotone"
                dataKey="created"
                name="Создано"
                stroke={palette.created}
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4 }}
              />
              <Line
                type="monotone"
                dataKey="completed"
                name="Выполнено"
                stroke={palette.completed}
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4 }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section className="rounded-2xl border border-gray-200 bg-white/70 p-5 shadow-xl backdrop-blur-md dark:border-gray-800 dark:bg-gray-900/70">
        <h3 className="text-sm font-semibold tracking-tight">Задачи по категориям</h3>
        {data && data.by_category.length > 0 ? (
          <div className="mt-4 h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={data.by_category}
                layout="vertical"
                margin={{ top: 4, right: 24, bottom: 0, left: 8 }}
              >
                <CartesianGrid stroke={palette.grid} strokeDasharray="3 3" horizontal={false} />
                <XAxis
                  type="number"
                  allowDecimals={false}
                  tick={{ fontSize: 11, fill: palette.text }}
                  stroke={palette.grid}
                />
                <YAxis
                  type="category"
                  dataKey="name"
                  width={120}
                  tick={{ fontSize: 11, fill: palette.text }}
                  stroke={palette.grid}
                />
                <Tooltip contentStyle={tooltipStyle} cursor={{ fillOpacity: 0.08 }} />
                {/* Одна серия — легенда не нужна: заголовок называет её. */}
                <Bar dataKey="total" name="Задач" radius={[0, 4, 4, 0]} barSize={14}>
                  {data.by_category.map((slice) => (
                    <Cell key={slice.name} fill={palette.created} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <p className="mt-3 text-xs text-gray-500 dark:text-gray-400">
            Пока нет данных — добавьте задачи и категории.
          </p>
        )}

        {/* Таблица рядом с графиком: значения доступны без наведения. */}
        {data && data.by_category.length > 0 && (
          <table className="mt-4 w-full text-left text-xs">
            <thead className="text-gray-500 dark:text-gray-400">
              <tr>
                <th className="py-1 font-medium">Категория</th>
                <th className="py-1 font-medium">Всего</th>
                <th className="py-1 font-medium">Выполнено</th>
              </tr>
            </thead>
            <tbody>
              {data.by_category.map((slice) => (
                <tr key={slice.name} className="border-t border-gray-100 dark:border-gray-800">
                  <td className="py-1">{slice.name}</td>
                  <td className="py-1">{slice.total}</td>
                  <td className="py-1">{slice.completed}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
};
