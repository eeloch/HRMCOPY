import type { ReactNode } from "react";

type AppCardProps = {
  children: ReactNode;
  className?: string;
  title?: ReactNode;
  actions?: ReactNode;
};

export function AppCard({ children, className = "", title, actions }: AppCardProps) {
  return (
    <div className={`rounded-2xl border border-slate-200 bg-white p-5 shadow-sm ${className}`}>
      {(title || actions) && (
        <div className="mb-5 flex flex-col gap-3 border-b border-slate-200 pb-4 sm:flex-row sm:items-center sm:justify-between">
          {title && <div className="font-bold text-slate-900">{title}</div>}
          {actions && <div className="flex items-center gap-3">{actions}</div>}
        </div>
      )}
      {children}
    </div>
  );
}
