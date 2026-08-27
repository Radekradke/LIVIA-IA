import Link from "next/link";
import type { ComponentPropsWithoutRef, ReactNode } from "react";

type Variant = "primary" | "outline" | "quiet";
type Size = "md" | "sm";

type SharedProps = {
  variant?: Variant;
  size?: Size;
  /** Glifo decorativo à direita; some para tecnologia assistiva. */
  arrow?: string;
  children: ReactNode;
};

function inner(children: ReactNode, arrow?: string) {
  return (
    <>
      <span>{children}</span>
      {arrow ? (
        <span className="btn-arrow" aria-hidden="true">
          {arrow}
        </span>
      ) : null}
    </>
  );
}

/** Ação que navega. */
export function ButtonLink({
  href,
  variant = "outline",
  size = "md",
  arrow,
  children,
  download,
  ...rest
}: SharedProps &
  Omit<ComponentPropsWithoutRef<typeof Link>, "children"> & {
    download?: boolean;
  }) {
  const external = typeof href === "string" && href.startsWith("/assets");
  const props = {
    className: "btn",
    "data-variant": variant,
    "data-size": size,
    ...rest,
  };

  if (external || download) {
    return (
      <a href={String(href)} download={download} {...props}>
        {inner(children, arrow)}
      </a>
    );
  }

  return (
    <Link href={href} {...props}>
      {inner(children, arrow)}
    </Link>
  );
}

/** Ação que não navega. `disabled` fica em aria-disabled para seguir focável. */
export function Button({
  variant = "outline",
  size = "md",
  arrow,
  disabled,
  children,
  onClick,
  ...rest
}: SharedProps & ComponentPropsWithoutRef<"button">) {
  return (
    <button
      type="button"
      className="btn"
      data-variant={variant}
      data-size={size}
      aria-disabled={disabled || undefined}
      onClick={disabled ? undefined : onClick}
      {...rest}
    >
      {inner(children, arrow)}
    </button>
  );
}

/** Estado indisponível que não é um controle — evita foco em nada acionável. */
export function ButtonPlaceholder({
  children,
  size = "md",
}: {
  children: ReactNode;
  size?: Size;
}) {
  return (
    <span className="btn" data-variant="outline" data-size={size} aria-disabled="true">
      <span>{children}</span>
    </span>
  );
}
