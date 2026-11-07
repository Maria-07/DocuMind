"use client";

// Chart primitives following the dataviz method: fixed categorical slot order (--series-1..3,
// validated), one y-axis per chart, 2px lines, rounded bar ends at the baseline, recessive
// grid/axes, hover tooltips by default, a legend for >= 2 series, text in ink tokens (never series color).

import type { ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export interface Series {
  key: string;
  label: string;
  /** CSS color, e.g. "var(--series-1)" — assign slots in fixed order per chart. */
  color: string;
}

const axis = { stroke: "var(--viz-axis)", fontSize: 11, tickLine: false, axisLine: { stroke: "var(--viz-grid)" } };

function ChartTooltip({
  active,
  payload,
  label,
  format,
  labelFormat,
}: {
  active?: boolean;
  payload?: { dataKey: string; name: string; value: number | null; color: string }[];
  label?: string | number;
  format: (v: number) => string;
  labelFormat?: (l: string | number) => string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border bg-popover px-3 py-2 text-xs text-popover-foreground shadow-md">
      <p className="mb-1 font-medium">{labelFormat ? labelFormat(label ?? "") : label}</p>
      {payload.map((p) => (
        <p key={p.dataKey} className="flex items-center gap-2">
          <span className="size-2 rounded-full" style={{ background: p.color }} aria-hidden />
          <span className="text-muted-foreground">{p.name}</span>
          <span className="ml-auto pl-3 font-medium tabular-nums">{p.value == null ? "—" : format(p.value)}</span>
        </p>
      ))}
    </div>
  );
}

function legendText(value: string) {
  return <span className="text-xs text-muted-foreground">{value}</span>;
}

export function LineTrend({
  data,
  xKey,
  series,
  format = (v) => v.toLocaleString(),
  xFormat,
  yDomain,
  height = 240,
}: {
  data: Record<string, unknown>[];
  xKey: string;
  series: Series[];
  format?: (v: number) => string;
  xFormat?: (v: string | number) => string;
  yDomain?: [number, number];
  height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="var(--viz-grid)" vertical={false} />
        <XAxis dataKey={xKey} {...axis} tickFormatter={xFormat} minTickGap={24} />
        <YAxis {...axis} width={44} tickFormatter={(v: number) => format(v)} domain={yDomain} allowDecimals={false} />
        <Tooltip
          cursor={{ stroke: "var(--viz-axis)", strokeWidth: 1 }}
          content={<ChartTooltip format={format} labelFormat={xFormat} />}
        />
        {series.length > 1 && <Legend iconType="circle" iconSize={8} formatter={legendText} />}
        {series.map((s) => (
          <Line
            key={s.key}
            dataKey={s.key}
            name={s.label}
            stroke={s.color}
            strokeWidth={2}
            dot={data.length <= 12 ? { r: 4, strokeWidth: 2, stroke: "var(--card)", fill: s.color } : false}
            activeDot={{ r: 5, strokeWidth: 2, stroke: "var(--card)" }}
            connectNulls
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

export function Bars({
  data,
  xKey,
  series,
  format = (v) => v.toLocaleString(),
  xFormat,
  layout = "horizontal",
  height = 240,
}: {
  data: Record<string, unknown>[];
  xKey: string;
  series: Series[];
  format?: (v: number) => string;
  xFormat?: (v: string | number) => string;
  layout?: "horizontal" | "vertical";
  height?: number;
}) {
  const vertical = layout === "vertical";
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout={layout} margin={{ top: 8, right: 12, bottom: 0, left: 0 }} barCategoryGap="30%">
        <CartesianGrid stroke="var(--viz-grid)" vertical={vertical} horizontal={!vertical} />
        {vertical ? (
          <>
            <XAxis type="number" {...axis} tickFormatter={(v: number) => format(v)} />
            <YAxis type="category" dataKey={xKey} {...axis} width={88} />
          </>
        ) : (
          <>
            <XAxis dataKey={xKey} {...axis} tickFormatter={xFormat} minTickGap={24} />
            <YAxis {...axis} width={44} tickFormatter={(v: number) => format(v)} allowDecimals={false} />
          </>
        )}
        <Tooltip cursor={{ fill: "var(--muted)", opacity: 0.5 }} content={<ChartTooltip format={format} labelFormat={xFormat} />} />
        {series.length > 1 && <Legend iconType="circle" iconSize={8} formatter={legendText} />}
        {series.map((s) => (
          <Bar
            key={s.key}
            dataKey={s.key}
            name={s.label}
            fill={s.color}
            stroke="var(--card)"
            strokeWidth={1}
            radius={vertical ? [0, 4, 4, 0] : [4, 4, 0, 0]}
            maxBarSize={28}
            isAnimationActive={false}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

export function StatTile({
  label,
  value,
  hint,
  icon,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="rounded-xl border bg-card p-4">
      <div className="flex items-center justify-between text-sm text-muted-foreground">
        <span>{label}</span>
        {icon}
      </div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-muted-foreground">{hint}</div>}
    </div>
  );
}

export const pct = (v: number) => `${Math.round(v * 100)}%`;
export const ms = (v: number) => (v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`);
export const shortDate = (v: string | number) =>
  new Date(`${v}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
