"use client";

import { useSurface } from "./surface";

/** Alterna a superfície de leitura. O rótulo diz o destino, não o estado atual. */
export function SurfaceToggle() {
  const { surface, toggle } = useSurface();
  const goingToPaper = surface === "night";

  return (
    <button
      type="button"
      className="surface-toggle"
      onClick={toggle}
      aria-pressed={surface === "paper"}
      title={goingToPaper ? "Ler em papel claro" : "Ler em tela escura"}
    >
      <span className="surface-toggle-dot" aria-hidden="true" />
      <span>{goingToPaper ? "Papel" : "Noite"}</span>
    </button>
  );
}
