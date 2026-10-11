import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function percent(value: number, digits = 0) {
  return `${(value * 100).toFixed(digits)}%`;
}

export function plural(count: number, noun: string) {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

/** The repository: links to code, issues and the Action. */
export const REPO = "https://github.com/Pradyuman-aviator/Karma";
