/**
 * PATCH /api/memory/[id] — proxy to FastAPI PATCH /memory/{id}
 * Issue #37
 */
import { json } from '@sveltejs/kit';
import { apiFetch } from '$lib/api/client.js';
import type { MemoryEntry } from '$lib/types/api.js';
import type { RequestHandler } from './$types.js';

export const PATCH: RequestHandler = async ({ params, request }) => {
	const body = await request.json();
	const result = await apiFetch<MemoryEntry>(`/memory/${params.id}`, {
		method: 'PATCH',
		body: JSON.stringify(body)
	});
	if (!result.ok) {
		return json({ data: null, errors: result.errors }, { status: result.status });
	}
	return json({ data: result.data, errors: [] });
};
