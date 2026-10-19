import type { SVGProps } from "react";

const paths = {
  chat: <><path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5Z" /></>,
  data: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 10h18M9 4v16" /></>,
  evaluate: <><path d="M9 4H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-4" /><rect x="9" y="2" width="6" height="4" rx="1" /><path d="m8 13 3 3 5-6" /></>,
  history: <><path d="M3 10a9 9 0 1 1 1.8 8.4M3 4v6h6M12 7v5l3 2" /></>,
  settings: <><path d="M4 7h16M4 17h16" /><circle cx="8" cy="7" r="3" /><circle cx="16" cy="17" r="3" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  arrow: <path d="M12 19V5m-6 6 6-6 6 6" />,
  right: <path d="M5 12h14m-6-6 6 6-6 6" />,
  chevron: <path d="m9 5 7 7-7 7" />,
  menu: <path d="M4 6h16M4 12h16M4 18h16" />,
  close: <path d="m6 6 12 12M6 18 18 6" />,
  logout: <><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" /></>,
  copy: <><rect x="8" y="8" width="12" height="13" rx="2" /><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" /></>,
  check: <path d="m5 12 4 4L19 6" />,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7h.01" /></>,
} as const;

export type IconName = keyof typeof paths;

export function Icon({ name, size = 20, ...props }: SVGProps<SVGSVGElement> & { name: IconName; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{paths[name]}</svg>;
}
