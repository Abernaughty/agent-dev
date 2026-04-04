/**
 * GET /api/filesystem/browse — proxy to FastAPI GET /filesystem/browse
 * Issue #106: Directory browser for workspace selector
 */
import { json } from '@sveltejs/kit';
import { apiFetch } from '$lib/api/client.js';
import type { RequestHandler } from './$types.js';

export const GET: RequestHandler = async ({ url }) => {
	const path = url.searchParams.get('path') || '';
	const showHidden = url.searchParams.get('show_hidden') || 'false';
	const query = new URLSearchParams();
	if (path) query.set('path', path);
	if (showHidden === 'true') query.set('show_hidden', 'true');

	const qs = query.toString();
	const result = await apiFetch<unknown>(`/filesystem/browse${qs ? `?${qs}` : ''}`);
	if (!result.ok) {
		return json({ data: null, errors: result.errors }, { status: result.status });
	}
	return json({ data: result.data, errors: [] });
};
