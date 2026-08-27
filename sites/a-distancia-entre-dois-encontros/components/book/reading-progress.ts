"use client";

import { useEffect, useState } from "react";

const COMPLETION_THRESHOLD = 0.94;

export const progressKey = (n: number) => `chapter-${n}-scroll-position`;
export const completedKey = (n: number) => `chapter-${n}-completed`;

function read(key: string) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* sem storage: a leitura funciona, só não é lembrada */
  }
}

/**
 * Progresso de leitura: restaura a posição, salva enquanto rola e marca
 * o capítulo como lido. Um único listener passivo, com rAF.
 */
export function useReadingProgress(chapterNumber: number) {
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    const saved = Number(read(progressKey(chapterNumber)) ?? 0);
    if (saved > 0) {
      requestAnimationFrame(() => window.scrollTo(0, saved));
    }

    let ticking = false;
    const update = () => {
      ticking = false;
      const total = document.documentElement.scrollHeight - window.innerHeight;
      const ratio = total > 0 ? window.scrollY / total : 0;
      setProgress(Math.min(Math.max(ratio, 0), 1));
      write(progressKey(chapterNumber), String(Math.round(window.scrollY)));
      if (ratio > COMPLETION_THRESHOLD) {
        write(completedKey(chapterNumber), "true");
      }
    };

    const onScroll = () => {
      if (!ticking) {
        ticking = true;
        requestAnimationFrame(update);
      }
    };

    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, [chapterNumber]);

  return progress;
}

const SERVER_SNAPSHOT = "";

export function readCompletion(count: number) {
  let out = "";
  for (let i = 1; i <= count; i += 1) out += read(completedKey(i)) ? "1" : "0";
  return out;
}

export { SERVER_SNAPSHOT };
