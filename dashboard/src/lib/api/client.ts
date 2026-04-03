/**
 * API client for the Dev Suite FastAPI backend.
 *
 * Used by SvelteKit server routes (+server.ts) to proxy requests.
 * Injects auth headers, handles errors, and normalises responses.
 *
 * Issue #37: SvelteKit API Routes & Providers
 * Issue #106: Fix FastAPI 422 detail parsing (array-of-objects)
 */

import { BACKEND_URL, API_SECRET } from '$env/static/private';

/** Backend base URL with trailing slash stripped. */
const BASE = (BACKEND_URL || 'http://localhost:8000').replace(/\/+$/, '');

/** Common headers for all backend requests. */
function authHeaders(): Record<string, string> {
	const headers: Record<string, string> = {
		'Content-Type': 'application/json'
	};
	if (API_SECRET) {
		headers['Authorization'] = `Bearer ${API_SECRET}`;
	}
	return headers;
}

export interface ApiResult<T = unknown> {
	ok: boolean;
	status: number;
	data: T | null;
	errors: string[];
}

/**
 * Normalise a FastAPI error detail into a human-readable string.
 *
 * FastAPI returns `detail` as a string for most errors, but 422
 * validation errors return an array of objects like:
 *   [{ "loc": ["body", "workspace"], "msg": "Field required", "type": "missing" }]
 *
 * Issue #106: Prevents [object Object] from reaching the UI.
 */
function normaliseDetail(detail: unknown): string {
	if (typeof detail === 'string') return detail;
	if (Array.isArray(detail)) {
		return detail
			.map((e) => {
				if (typeof e === 'string') return e;
				if (e && typeof e === 'object') {
					const loc = Array.isArray(e.loc) ? e.loc.join('.') : '';
					const msg = e.msg || JSON.stringify(e);
					return loc ? `${loc}: ${msg}` : msg;
				}
				return JSON.stringify(e);
			})
			.join('; ');
	}
	return JSON.stringify(detail);
}

/**
 * Make a JSON request to the FastAPI backend.
 *
 * Returns a normalised result — never throws. Callers can check
 * `result.ok` and translate to SvelteKit `json()` / `error()`.
 */
export async function apiFetch<T = unknown>(
	path: string,
	init?: RequestInit
): Promise<ApiResult<T>> {
	const url = `${BASE}${path}`;

	try {
		const res = await fetch(url, {
			...init,
			headers: {
				...authHeaders(),
				...(init?.headers || {})
			}
		});

		if (!res.ok) {
			// Try to parse error detail from FastAPI
			let detail = `Backend returned ${res.status}`;
			try {
				const body = await res.json();
				if (body.detail) detail = normaliseDetail(body.detail);
			} catch {
				// body wasn't JSON — use status text
				detail = res.statusText || detail;
			}
			return { ok: false, status: res.status, data: null, errors: [detail] };
		}

		const body = await res.json();
		return {
			ok: true,
			status: res.status,
			data: body.data ?? body,
			errors: body.errors ?? []
		};
	} catch (err) {
		// Network error — backend unreachable
		const message = err instanceof Error ? err.message : 'Backend unreachable';
		return { ok: false, status: 502, data: null, errors: [message] };
	}
}

/**
 * Open an SSE stream to the backend's /stream endpoint.
 *
 * Returns the raw Response so the SvelteKit route can pipe it
 * through to the browser as a streaming response.
 */
export async function apiStream(): Promise<Response> {
	const url = `${BASE}/stream`;
	return fetch(url, {
		headers: {
			...authHeaders(),
			Accept: 'text/event-stream'
		}
	});
}

/** Re-export the base URL for logging / health checks. */
export { BASE as BACKEND_BASE_URL };
