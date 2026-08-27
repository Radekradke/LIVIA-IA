"use client";

import { useCallback, useSyncExternalStore } from "react";

export type Surface = "night" | "paper";

export const SURFACE_KEY = "adee-surface";

/**
 * Script inline executado antes da primeira pintura: evita o flash de tema.
 *
 * Padrão do site é a noite; o padrão do leitor é o papel — abrir um capítulo
 * é acender a luz. Uma escolha explícita do leitor vence os dois e vale para
 * o site inteiro.
 */
export const SURFACE_BOOT_SCRIPT = `try{var s=localStorage.getItem("${SURFACE_KEY}");var r=/^\\/capitulo-/.test(location.pathname);var paper=s?s==="paper":r;var d=document.documentElement;if(paper){d.dataset.surface="paper";d.style.colorScheme="light"}else{d.style.colorScheme="dark"}}catch(e){}`;

const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((fn) => fn());
}

function subscribe(onChange: () => void) {
  listeners.add(onChange);
  window.addEventListener("storage", onChange);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("storage", onChange);
  };
}

function getSnapshot(): Surface {
  return document.documentElement.dataset.surface === "paper" ? "paper" : "night";
}

export function useSurface() {
  const surface = useSyncExternalStore(subscribe, getSnapshot, () => "night" as Surface);

  const toggle = useCallback(() => {
    const next: Surface = getSnapshot() === "paper" ? "night" : "paper";
    const root = document.documentElement;
    if (next === "paper") {
      root.dataset.surface = "paper";
      root.style.colorScheme = "light";
    } else {
      delete root.dataset.surface;
      root.style.colorScheme = "dark";
    }
    try {
      window.localStorage.setItem(SURFACE_KEY, next);
    } catch {
      /* modo privado: a escolha vale só para esta sessão */
    }
    emit();
  }, []);

  return { surface, toggle };
}
