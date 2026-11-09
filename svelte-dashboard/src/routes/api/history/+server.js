import { getRecentHistory, BACKEND, DB_PATH } from '$lib/backend.js';

export async function GET({ url }) {
	const limit = parseInt(url.searchParams.get('limit') || '100', 10);
	const search = url.searchParams.get('search') || '';
	const app = url.searchParams.get('app') || '';

	if (BACKEND === 'clickhouse') {
		try {
			let history = await getRecentHistory(limit);
			if (search) {
				const q = search.toLowerCase();
				history = history.filter(h =>
					h.window_title.toLowerCase().includes(q) || h.app_class.toLowerCase().includes(q)
				);
			}
			if (app) {
				history = history.filter(h => h.app_class === app);
			}
			return new Response(JSON.stringify(history), {
				headers: { 'Content-Type': 'application/json' }
			});
		} catch (e) {
			console.error('ClickHouse history error:', e);
			return new Response(JSON.stringify({ error: e.message }), { status: 500 });
		}
	}

	// SQLite fallback dla lokalnego trybu dev
	const { default: sqlite3 } = await import('sqlite3');
	return new Promise((resolve) => {
		const db = new sqlite3.Database(DB_PATH, sqlite3.OPEN_READONLY, (err) => {
			if (err) {
				resolve(new Response(JSON.stringify([]), { headers: { 'Content-Type': 'application/json' } }));
				return;
			}
		});

		let conditions = ['1=1', 'duration_ms > 0'];
		let params = [];

		if (search) {
			conditions.push('(window_title LIKE ? OR app_class LIKE ?)');
			params.push(`%${search}%`, `%${search}%`);
		}
		if (app) {
			conditions.push('app_class = ?');
			params.push(app);
		}

		params.push(limit);

		const query = `
			SELECT
				id,
				ts_start,
				ts_end,
				duration_ms,
				host,
				app_class,
				window_title,
				workspace,
				monitor,
				category,
				project,
				entity,
				synced
			FROM window_activity
			WHERE ${conditions.join(' AND ')}
			ORDER BY id DESC
			LIMIT ?
		`;

		db.all(query, params, (err, rows) => {
			db.close();
			if (err) {
				resolve(new Response(JSON.stringify({ error: err.message }), { status: 500 }));
				return;
			}
			resolve(new Response(JSON.stringify(rows || []), {
				headers: { 'Content-Type': 'application/json' }
			}));
		});
	});
}
