/**
 * GET  /api/tasks — proxy to FastAPI GET /tasks
 * POST /api/tasks — proxy to FastAPI POST /tasks
 * Issue #37
 */
import { json } from '@sveltejs/kit';
import { apiFetch } from '$lib/api/client.js';
import type { TaskSummary, CreateTaskResponse } from '$lib/types/api.js';
import type { RequestHandler } from './$types.js';

export const GET: RequestHandler = async () => {
	const result = await apiFetch<TaskSummary[]>('/tasks');
	if (!result.ok) {
		return json({ data: null, errors: result.errors }, { status: result.status });
	}
	return json({ data: result.data, errors: [] });
};

export const POST: RequestHandler = async ({ request }) => {
	const body = await request.json();
	const result = await apiFetch<CreateTaskResponse>('/tasks', {
		method: 'POST',
		body: JSON.stringify(body)
	});
	if (!result.ok) {
		return json({ data: null, errors: result.errors }, { status: result.status });
	}
	return json({ data: result.data, errors: [] }, { status: 201 });
};
