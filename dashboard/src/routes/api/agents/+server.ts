/**
 * GET /api/agents — proxy to FastAPI GET /agents
 * Issue #37
 */
import { json } from '@sveltejs/kit';
import { apiFetch } from '$lib/api/client.js';
import type { Agent } from '$lib/types/api.js';
import type { RequestHandler } from './$types.js';

export const GET: RequestHandler = async () => {
	const result = await apiFetch<Agent[]>('/agents');
	if (!result.ok) {
		return json({ data: null, errors: result.errors }, { status: result.status });
	}
	return json({ data: result.data, errors: [] });
};
