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
	let body: unknown;
	try {
		body = await request.json();
	} catch {
		return json({ data: null, errors: ['Invalid JSON body'] }, { status: 400 });
	}
	const result = await apiFetch<PlannerSessionResponse>('/tasks/plan', {
		method: 'POST',
		body: JSON.stringify(body)
	});
	if (!result.ok) {
		return json({ data: null, errors: result.errors }, { status: result.status });
	}
	return json({ data: result.data, errors: [] }, { status: 201 });
};
