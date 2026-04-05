/**
 * POST /api/planner/[id]/submit — proxy to FastAPI POST /tasks/plan/{id}/submit
 *
 * Submits a Planner session to the Architect, creating a task.
 *
 * Issue #106 Phase B
 */
import { json } from '@sveltejs/kit';
import { apiFetch } from '$lib/api/client.js';
import type { PlannerSubmitResponse } from '$lib/types/api.js';
import type { RequestHandler } from './$types.js';

export const POST: RequestHandler = async ({ params }) => {
	const result = await apiFetch<PlannerSubmitResponse>(
		`/tasks/plan/${params.id}/submit`,
		{ method: 'POST' }
	);
	if (!result.ok) {
		return json({ data: null, errors: result.errors }, { status: result.status });
	}
	return json({ data: result.data, errors: [] });
};
