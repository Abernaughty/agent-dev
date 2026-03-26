/**
 * GET /api/memory — proxy to FastAPI GET /memory
 * Issue #37
 */
import { json } from '@sveltejs/kit';
import { apiFetch } from '$lib/api/client.js';
import type { MemoryEntry } from '$lib/types/api.js';
import type { RequestHandler } from './$types.js';

export const GET: RequestHandler = async ({ url }) => {
	// Forward query params (tier, status) to backend
	const tier = url.searchParams.get('tier');
	const status = url.searchParams.get('status');
	const params = new URLSearchParams();
	if (tier) params.set('tier', tier);
	if (status) params.set('status', status);
	const qs = params.toString();
	const path = qs ? `/memory?${qs}` : '/memory';

	const result = await apiFetch<MemoryEntry[]>(path);
	if (!result.ok) {
		return json({ data: null, errors: result.errors }, { status: result.status });
	}
	return json({ data: result.data, errors: [] });
};
