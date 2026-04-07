/**
 * GET /api/prs/[number]/comments — proxy to FastAPI GET /prs/{number}/comments
 *
 * Returns issue + review comments merged and sorted by created_at.
 * Issue #109: PR lifecycle blade
 */
import { json } from '@sveltejs/kit';
import { apiFetch } from '$lib/api/client.js';
import type { PRComment } from '$lib/types/api.js';
import type { RequestHandler } from './$types.js';

export const GET: RequestHandler = async ({ params }) => {
	const prNumber = params.number;
	const result = await apiFetch<PRComment[]>(`/prs/${prNumber}/comments`);
	if (!result.ok) {
		return json({ data: null, errors: result.errors }, { status: result.status });
	}
	return json({ data: result.data, errors: [] });
};
