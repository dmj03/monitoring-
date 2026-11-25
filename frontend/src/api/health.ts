import { request } from "./client";
import type { Health } from "./types";

export const getHealth = () => request<Health>("/health");
