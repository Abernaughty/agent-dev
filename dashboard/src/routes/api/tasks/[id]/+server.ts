/**
 * GET /api/tasks/[id] — proxy to FastAPI GET /tasks/{id}
 * Issue #37
 */
import { json } from '@sveltejs/kit';
import { apiFetch } from '$lib/api/client.js';
import type { TaskDetail } from '$lib/types/api.js';
import type { RequestHandler } from './$types.js';

export const GET: RequestHandler = async ({ params }) => {
	const result = await apiFetch<TaskDetail>(`/tasks/${params.id}`);
	if (!result.ok) {
		return json({ data: null, errors: result.errors }, { status: result.status });
	}
	return json({ data: result.data, errors: [] });
};
