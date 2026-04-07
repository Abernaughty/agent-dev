/**
 * Pull Requests store — reactive PR list with detail fetching.
 *
 * Initialised by fetching GET /api/prs.
 * Polling-based refresh (PRs don't stream via SSE — GitHub doesn't push
 * PR updates to us, so polling is by design per the roadmap. Webhooks
 * are deferred to Phase 3).
 * Detail, files, reviews, and comments fetched on-demand.
 *
 * Issue #37: Initial store
 * Issue #109: Full lifecycle — detail fetch, files, reviews, comments,
 *            per-PR loading/error, cache, manual refresh, cross-nav
 * Issue #109 CR: Split-brain fix (bidirectional sync between list ↔ detailCache),
 *               separate draftCount from openCount
 */

import type {
	PullRequest,
	PRFileChange,
	PRReview,
	PRComment
} from '$lib/types/api.js';

let prs = $state<PullRequest[]>([]);
let loading = $state(false);
let error = $state<string | null>(null);

/** Polling interval in ms (30 seconds). */
const POLL_INTERVAL = 30_000;
let pollTimer: ReturnType<typeof setInterval> | null = null;

/** Per-PR detail cache (keyed by PR number). */
let detailCache = $state<Map<number, PullRequest>>(new Map());
let detailLoadingMap = $state<Map<number, boolean>>(new Map());
let detailErrorMap = $state<Map<number, string>>(new Map());

/** Per-PR files cache. */
let filesCache = $state<Map<number, PRFileChange[]>>(new Map());

/** Per-PR reviews cache. */
let reviewsCache = $state<Map<number, PRReview[]>>(new Map());

/** Per-PR comments cache. */
let commentsCache = $state<Map<number, PRComment[]>>(new Map());

/** Currently selected PR number (for cross-navigation). */
let selectedNumber = $state<number | null>(null);

/**
 * CR fix: Sync list → detailCache after a list refresh.
 * Any PR that exists in both the list and the cache gets its cache entry
 * updated with the fresher list data (preserving reviews/check_status
 * that only come from the detail endpoint).
 */
function syncListToDetailCache(freshList: PullRequest[]) {
	if (detailCache.size === 0) return;
	const nextCache = new Map(detailCache);
	let changed = false;
	for (const pr of freshList) {
		if (nextCache.has(pr.number)) {
			const cached = nextCache.get(pr.number)!;
			// Merge: use list data for summary fields, keep cached detail-only fields
			nextCache.set(pr.number, {
				...pr,
				reviews: cached.reviews?.length ? cached.reviews : pr.reviews ?? [],
				check_status: cached.check_status?.length ? cached.check_status : pr.check_status ?? []
			});
			changed = true;
		}
	}
	if (changed) detailCache = nextCache;
}

/**
 * CR fix: Sync detailCache → list after a detail fetch.
 * Updates the corresponding entry in the list so sidebar badges/counts
 * reflect the freshest data.
 */
function syncDetailToList(detail: PullRequest) {
	const idx = prs.findIndex((p) => p.number === detail.number);
	if (idx >= 0) {
		prs = prs.map((p, i) => (i === idx ? { ...p, ...detail } : p));
	}
}

export const prsStore = {
	get list() {
		return prs;
	},
	get loading() {
		return loading;
	},
	get error() {
		return error;
	},
	/** CR fix: openCount excludes drafts. */
	get openCount() {
		return prs.filter((p) => p.status === 'review' || p.status === 'open').length;
	},
	/** CR fix: separate draft count. */
	get draftCount() {
		return prs.filter((p) => p.status === 'draft').length;
	},
	get selectedNumber() {
		return selectedNumber;
	},

	/** Set the selected PR number (used for cross-navigation from task view). */
	selectPR(num: number) {
		selectedNumber = num;
	},

	clearSelection() {
		selectedNumber = null;
	},

	// -- List --

	/** Fetch PRs from the proxy route. */
	async refresh() {
		loading = true;
		error = null;
		try {
			const res = await fetch('/api/prs');
			const body = await res.json();
			if (res.ok && body.data) {
				prs = body.data;
				// CR fix: sync fresh list data into detailCache
				syncListToDetailCache(prs);
			} else {
				error = body.errors?.[0] ?? 'Failed to fetch PRs';
			}
		} catch (err) {
			error = err instanceof Error ? err.message : 'Network error';
		} finally {
			loading = false;
		}
	},

	// -- Detail --

	getDetail(prNumber: number): PullRequest | null {
		return detailCache.get(prNumber) ?? null;
	},

	isDetailLoading(prNumber: number): boolean {
		return detailLoadingMap.get(prNumber) ?? false;
	},

	getDetailError(prNumber: number): string | null {
		return detailErrorMap.get(prNumber) ?? null;
	},

	async fetchDetail(prNumber: number, force = false): Promise<PullRequest | null> {
		if (!force && detailCache.has(prNumber)) {
			return detailCache.get(prNumber)!;
		}

		const nextLoading = new Map(detailLoadingMap);
		nextLoading.set(prNumber, true);
		detailLoadingMap = nextLoading;

		const nextError = new Map(detailErrorMap);
		nextError.delete(prNumber);
		detailErrorMap = nextError;

		try {
			const res = await fetch(`/api/prs/${prNumber}`);
			const body = await res.json();

			if (res.ok && body.data) {
				const detail = body.data as PullRequest;
				const nextCache = new Map(detailCache);
				nextCache.set(prNumber, detail);
				detailCache = nextCache;
				// CR fix: sync detail into list
				syncDetailToList(detail);
				return detail;
			}
			const errMsg = body.errors?.[0] ?? 'Failed to fetch PR detail';
			const errMap = new Map(detailErrorMap);
			errMap.set(prNumber, errMsg);
			detailErrorMap = errMap;
			return null;
		} catch (err) {
			const errMsg = err instanceof Error ? err.message : 'Network error';
			const errMap = new Map(detailErrorMap);
			errMap.set(prNumber, errMsg);
			detailErrorMap = errMap;
			return null;
		} finally {
			const loadMap = new Map(detailLoadingMap);
			loadMap.delete(prNumber);
			detailLoadingMap = loadMap;
		}
	},

	// -- Files --

	getFiles(prNumber: number): PRFileChange[] | null {
		return filesCache.get(prNumber) ?? null;
	},

	async fetchFiles(prNumber: number, force = false): Promise<PRFileChange[] | null> {
		if (!force && filesCache.has(prNumber)) {
			return filesCache.get(prNumber)!;
		}
		try {
			const res = await fetch(`/api/prs/${prNumber}/files`);
			const body = await res.json();
			if (res.ok && body.data) {
				const files = body.data as PRFileChange[];
				const next = new Map(filesCache);
				next.set(prNumber, files);
				filesCache = next;
				return files;
			}
			return null;
		} catch {
			return null;
		}
	},

	// -- Reviews --

	getReviews(prNumber: number): PRReview[] | null {
		return reviewsCache.get(prNumber) ?? null;
	},

	async fetchReviews(prNumber: number, force = false): Promise<PRReview[] | null> {
		if (!force && reviewsCache.has(prNumber)) {
			return reviewsCache.get(prNumber)!;
		}
		try {
			const res = await fetch(`/api/prs/${prNumber}/reviews`);
			const body = await res.json();
			if (res.ok && body.data) {
				const reviews = body.data as PRReview[];
				const next = new Map(reviewsCache);
				next.set(prNumber, reviews);
				reviewsCache = next;
				return reviews;
			}
			return null;
		} catch {
			return null;
		}
	},

	// -- Comments --

	getComments(prNumber: number): PRComment[] | null {
		return commentsCache.get(prNumber) ?? null;
	},

	async fetchComments(prNumber: number, force = false): Promise<PRComment[] | null> {
		if (!force && commentsCache.has(prNumber)) {
			return commentsCache.get(prNumber)!;
		}
		try {
			const res = await fetch(`/api/prs/${prNumber}/comments`);
			const body = await res.json();
			if (res.ok && body.data) {
				const comments = body.data as PRComment[];
				const next = new Map(commentsCache);
				next.set(prNumber, comments);
				commentsCache = next;
				return comments;
			}
			return null;
		} catch {
			return null;
		}
	},

	// -- Manual Refresh --

	/** Full refresh: clear caches, re-fetch list. */
	async manualRefresh() {
		detailCache = new Map();
		filesCache = new Map();
		reviewsCache = new Map();
		commentsCache = new Map();
		await this.refresh();
	},

	/** Refresh a single PR's detail + files. */
	async refreshPR(prNumber: number) {
		await Promise.all([
			this.fetchDetail(prNumber, true),
			this.fetchFiles(prNumber, true),
			this.fetchReviews(prNumber, true),
			this.fetchComments(prNumber, true)
		]);
	},

	// -- Polling --

	/** Start polling for PR updates. */
	startPolling() {
		this.stopPolling();
		pollTimer = setInterval(() => this.refresh(), POLL_INTERVAL);
	},

	/** Stop polling. */
	stopPolling() {
		if (pollTimer) {
			clearInterval(pollTimer);
			pollTimer = null;
		}
	},

	/** Reset to empty state. */
	reset() {
		prs = [];
		error = null;
		selectedNumber = null;
		detailCache = new Map();
		detailLoadingMap = new Map();
		detailErrorMap = new Map();
		filesCache = new Map();
		reviewsCache = new Map();
		commentsCache = new Map();
		this.stopPolling();
	}
};
