/**
 * POST /api/planner/[id]/submit — proxy to FastAPI POST /tasks/plan/{id}/submit
 *
 * Submits a Planner session to the Architect, creating a task.
 * Issue #153: forwards create_pr and workspace type fields in the request body.
 *
 * Issue #106 Phase B
 */
import { json } from '@sveltejs/kit';
import { apiFetch } from '$lib/api/client.js';
import type { PlannerSubmitResponse } from '$lib/types/api.js';
import type { RequestHandler } from './$types.js';

export const POST: RequestHandler = async ({ params, request }) => {
	// Read optional body (may be empty for backwards compat)
	let body: Record<string, unknown> | undefined;
	try {
		const text = await request.text();
		if (text) body = JSON.parse(text);
	} catch {
		// No body or invalid JSON — proceed without it
	}

	const result = await apiFetch<PlannerSubmitResponse>(
		`/tasks/plan/${params.id}/submit`,
		{
			method: 'POST',
			headers: body ? { 'Content-Type': 'application/json' } : undefined,
			body: body ? JSON.stringify(body) : undefined,
		}
	);
	if (!result.ok) {
		return json({ data: null, errors: result.errors }, { status: result.status });
	}
	return json({ data: result.data, errors: [] });
};
