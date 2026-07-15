import axios, { AxiosInstance, AxiosError } from "axios";
import type { ApiError } from "@/types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

const client: AxiosInstance = axios.create({
  baseURL: BASE_URL,
  timeout: 180_000, // Granite calls can take up to 2 min
  headers: { "Content-Type": "application/json" },
});

// Normalise every API error to a typed ApiError object
client.interceptors.response.use(
  (res) => res,
  (err: AxiosError<{ detail?: string }>) => {
    const apiError: ApiError = {
      message: err.message,
      status: err.response?.status,
      detail: err.response?.data?.detail,
    };
    return Promise.reject(apiError);
  }
);

export default client;
