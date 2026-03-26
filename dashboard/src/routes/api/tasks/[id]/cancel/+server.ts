/**
 * POST /api/tasks/[id]/cancel — proxy to FastAPI POST /tasks/{id}/cancel
 * Issue #37
 */
import { json } from '@sveltejs/kit';
import { apiFetch } from '$lib/api/client.js';
import type { RequestHandler } from './$types.js';

export const POST: RequestHandler = async ({ params }) => {
	const result = await apiFetch(`/tasks/${params.id}/cancel`, { method: 'POST' });
	if (!result.ok) {
		return json({ data: null, errors: result.errors }, { status: result.status });
	}
	return json({ data: result.data, errors: [] });
};
