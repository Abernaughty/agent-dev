/**
 * GET /api/prs/[number] — proxy to FastAPI GET /prs/{number}
 *
 * Returns full PR detail including reviews and check status.
 * Issue #109: PR lifecycle blade
 */
import { json } from '@sveltejs/kit';
import { apiFetch } from '$lib/api/client.js';
import type { PullRequest } from '$lib/types/api.js';
import type { RequestHandler } from './$types.js';

export const GET: RequestHandler = async ({ params }) => {
	const prNumber = params.number;
	const result = await apiFetch<PullRequest>(`/prs/${prNumber}`);
	if (!result.ok) {
		return json({ data: null, errors: result.errors }, { status: result.status });
	}
	return json({ data: result.data, errors: [] });
};
