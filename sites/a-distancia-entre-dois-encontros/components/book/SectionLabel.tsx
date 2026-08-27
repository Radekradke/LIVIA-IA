import type { ReactNode } from "react";

export function SectionLabel({
  children,
  tick = true,
  id,
}: {
  children: ReactNode;
  tick?: boolean;
  id?: string;
}) {
  return (
    <p className="u-label" data-tick={tick ? undefined : "none"} id={id}>
      {children}
    </p>
  );
}
