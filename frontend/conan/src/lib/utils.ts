import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Renders API constants like "FAN_IN_SPIKE" / "SCATTER-GATHER" as "Fan In Spike" / "Scatter-Gather". */
export function titleCase(value: string): string {
  return value
    .toLowerCase()
    .replace(/_/g, " ")
    .replace(/(^|[\s-])([a-z])/g, (_, sep, char) => sep + char.toUpperCase())
}
