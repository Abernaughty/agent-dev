/**
 * POST /api/planner — proxy to FastAPI POST /tasks/plan
 *
 * Starts a new Planner session with workspace context.
 *
 * Issue #106 Phase B
 */
import { json } from '@sveltejs/kit';
import { apiFetch } from '$lib/api/client.js';
import type { PlannerSessionResponse } from '$lib/types/api.js';
import type { RequestHandler } from './$types.js';

export const POST: RequestHandler = async ({ request }) => {
	const body = await request.json();
	const result = await apiFetch<PlannerSessionResponse>('/tasks/plan', {
		method: 'POST',
		body: JSON.stringify(body)
	});
	if (!result.ok) {
		return json({ data: null, errors: result.errors }, { status: result.status });
	}
	return json({ data: result.data, errors: [] }, { status: 201 });
};
