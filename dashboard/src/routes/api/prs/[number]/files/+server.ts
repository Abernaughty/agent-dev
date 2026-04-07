/**
 * GET /api/prs/[number]/files — proxy to FastAPI GET /prs/{number}/files
 *
 * Returns file changes with unified diff patches.
 * Issue #109: PR lifecycle blade
 */
import { json } from '@sveltejs/kit';
import { apiFetch } from '$lib/api/client.js';
import type { PRFileChange } from '$lib/types/api.js';
import type { RequestHandler } from './$types.js';

export const GET: RequestHandler = async ({ params }) => {
	const prNumber = params.number;
	const result = await apiFetch<PRFileChange[]>(`/prs/${prNumber}/files`);
	if (!result.ok) {
		return json({ data: null, errors: result.errors }, { status: result.status });
	}
	return json({ data: result.data, errors: [] });
};
