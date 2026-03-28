/**
 * GET /api/memory/audit — proxy to FastAPI GET /memory/audit
 * Issue #19
 */
import { json } from '@sveltejs/kit';
import { apiFetch } from '$lib/api/client.js';
import type { AuditLogEntry } from '$lib/types/api.js';
import type { RequestHandler } from './$types.js';

export const GET: RequestHandler = async ({ url }) => {
	const limit = url.searchParams.get('limit');
	const params = new URLSearchParams();
	if (limit) params.set('limit', limit);
	const qs = params.toString();
	const path = qs ? `/memory/audit?${qs}` : '/memory/audit';

	const result = await apiFetch<AuditLogEntry[]>(path);
	if (!result.ok) {
		return json({ data: null, errors: result.errors }, { status: result.status });
	}
	return json({ data: result.data, errors: [] });
};
