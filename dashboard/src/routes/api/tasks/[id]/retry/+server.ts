/**
 * POST /api/tasks/[id]/retry — proxy to FastAPI POST /tasks/{id}/retry
 * Issue #37
 */
import { json } from '@sveltejs/kit';
import { apiFetch } from '$lib/api/client.js';
import type { RequestHandler } from './$types.js';

export const POST: RequestHandler = async ({ params }) => {
	const result = await apiFetch(`/tasks/${params.id}/retry`, { method: 'POST' });
	if (!result.ok) {
		return json({ data: null, errors: result.errors }, { status: result.status });
	}
	return json({ data: result.data, errors: [] });
};
