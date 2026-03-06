import axios, { type AxiosInstance } from "axios";

export interface ApiClientOptions {
	baseURL?: string;
}

function getDefaultBaseUrl(): string {
	const envBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL;
	if (envBaseUrl && envBaseUrl.trim().length > 0) return envBaseUrl;
	return "http://127.0.0.1:8000";
}

export function createApiClient(options: ApiClientOptions = {}): AxiosInstance {
	return axios.create({
		baseURL: options.baseURL ?? getDefaultBaseUrl(),
		withCredentials: false,
		headers: {
			"Content-Type": "application/json"
		}
	});
}

export const api = createApiClient();
