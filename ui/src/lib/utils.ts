import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatMoney(v: number): string {
  return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
