/**
 * GET /api/prs/[number]/reviews — proxy to FastAPI GET /prs/{number}/reviews
 *
 * Returns reviews on the PR (including CodeRabbit bot reviews).
 * Issue #109: PR lifecycle blade
 */
import { json } from '@sveltejs/kit';
import { apiFetch } from '$lib/api/client.js';
import type { PRReview } from '$lib/types/api.js';
import type { RequestHandler } from './$types.js';

export const GET: RequestHandler = async ({ params }) => {
	const prNumber = params.number;
	const result = await apiFetch<PRReview[]>(`/prs/${prNumber}/reviews`);
	if (!result.ok) {
		return json({ data: null, errors: result.errors }, { status: result.status });
	}
	return json({ data: result.data, errors: [] });
};
