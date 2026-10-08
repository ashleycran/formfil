import asyncpg
from config import DATABASE_URL

_pool = None

async def init_db():
    global _pool
    _pool = await asyncpg.create_pool(DATABASE_URL)
    
    async with _pool.acquire() as conn:
        # Create authorized_users table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS authorized_users (
                telegram_id BIGINT PRIMARY KEY,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                active BOOLEAN DEFAULT TRUE
            )
        ''')
        
        # Create users table (profile)
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE,
                full_name TEXT,
                first_name TEXT,
                last_name TEXT,
                email TEXT,
                phone TEXT,
                company TEXT,
                website TEXT,
                subject TEXT,
                message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create websites table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS websites (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT,
                url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(telegram_id, url)
            )
        ''')
        
        # Create results table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS results (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT,
                website_id INTEGER,
                url TEXT,
                status TEXT,
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create scheduler table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS schedules (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE,
                hour INTEGER NOT NULL,
                minute INTEGER NOT NULL DEFAULT 0,
                timezone TEXT NOT NULL DEFAULT 'UTC',
                active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create retry_queue table
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS retry_queue (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT,
                url TEXT,
                attempt INTEGER DEFAULT 1,
                retry_after TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(telegram_id, url)
            )
        ''')

async def is_user_authorized(telegram_id, admin_id):
    if telegram_id == admin_id:
        return True
        
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT active FROM authorized_users WHERE telegram_id = $1", telegram_id)
        if row and row['active']:
            return True
        return False

async def get_user_profile(telegram_id):
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE telegram_id = $1", telegram_id)
        if row:
            return dict(row)
        return {}

_ALLOWED_PROFILE_COLUMNS = frozenset({
    "full_name", "first_name", "last_name", "email",
    "phone", "company", "website", "subject", "message",
})

async def update_user_profile(telegram_id, **kwargs):
    # Whitelist column names to prevent SQL injection via kwarg keys
    invalid = set(kwargs.keys()) - _ALLOWED_PROFILE_COLUMNS
    if invalid:
        raise ValueError(f"update_user_profile: disallowed column(s): {invalid}")

    async with _pool.acquire() as conn:
        # Ensure user exists
        await conn.execute("INSERT INTO users (telegram_id) VALUES ($1) ON CONFLICT DO NOTHING", telegram_id)

        if kwargs:
            set_clause = ", ".join([f"{k} = ${i+1}" for i, k in enumerate(kwargs.keys())])
            values = list(kwargs.values())
            values.append(telegram_id)
            query = f"UPDATE users SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE telegram_id = ${len(values)}"
            await conn.execute(query, *values)

async def clear_user_profile(telegram_id):
    async with _pool.acquire() as conn:
        await conn.execute('''
            UPDATE users SET
            full_name = NULL, first_name = NULL, last_name = NULL, email = NULL,
            phone = NULL, company = NULL, website = NULL, subject = NULL
            WHERE telegram_id = $1
        ''', telegram_id)

async def get_websites(telegram_id):
    async with _pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, url FROM websites WHERE telegram_id = $1 ORDER BY id", telegram_id)
        return [dict(row) for row in rows]

async def add_website(telegram_id, url):
    async with _pool.acquire() as conn:
        try:
            await conn.execute("INSERT INTO websites (telegram_id, url) VALUES ($1, $2)", telegram_id, url)
            return True
        except asyncpg.exceptions.UniqueViolationError:
            return False

async def remove_website(telegram_id, website_id=None, url=None):
    async with _pool.acquire() as conn:
        if website_id:
            await conn.execute("DELETE FROM websites WHERE telegram_id = $1 AND id = $2", telegram_id, website_id)
        elif url:
            await conn.execute("DELETE FROM websites WHERE telegram_id = $1 AND url = $2", telegram_id, url)

async def get_authorized_users():
    async with _pool.acquire() as conn:
        rows = await conn.fetch("SELECT telegram_id, added_at, active FROM authorized_users ORDER BY added_at")
        return [dict(row) for row in rows]

async def authorize_user(telegram_id):
    async with _pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO authorized_users (telegram_id, active) 
            VALUES ($1, TRUE) 
            ON CONFLICT (telegram_id) DO UPDATE SET active = TRUE
        ''', telegram_id)

async def unauthorize_user(telegram_id):
    async with _pool.acquire() as conn:
        await conn.execute("UPDATE authorized_users SET active = FALSE WHERE telegram_id = $1", telegram_id)

async def add_result(telegram_id, url, status, reason=""):
    async with _pool.acquire() as conn:
        await conn.execute("INSERT INTO results (telegram_id, url, status, reason) VALUES ($1, $2, $3, $4)", 
                      telegram_id, url, status, reason)

async def get_results_summary(telegram_id):
    async with _pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT status, COUNT(*) as count 
            FROM results 
            WHERE telegram_id = $1 
            GROUP BY status
        ''', telegram_id)
        return {row['status']: row['count'] for row in rows}

async def get_results_by_status(telegram_id: int, statuses: list) -> list:
    """Return all results matching any of the given statuses, newest first."""
    async with _pool.acquire() as conn:
        placeholders = ", ".join(f"${i+2}" for i in range(len(statuses)))
        rows = await conn.fetch(
            f"SELECT url, status, reason, created_at FROM results "
            f"WHERE telegram_id = $1 AND status = ANY(ARRAY[{placeholders}]::text[]) "
            f"ORDER BY id DESC",
            telegram_id, *statuses,
        )
        return [dict(row) for row in rows]

async def get_recent_results(telegram_id, limit=10):
    async with _pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT url, status, reason, created_at 
            FROM results 
            WHERE telegram_id = $1 
            ORDER BY id DESC LIMIT $2
        ''', telegram_id, limit)
        return [dict(row) for row in rows]


# ── Scheduler ─────────────────────────────────────────────────────────────────

async def get_schedule(telegram_id: int):
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM schedules WHERE telegram_id = $1", telegram_id
        )
        return dict(row) if row else None

async def set_schedule(telegram_id: int, hour: int, minute: int, timezone: str = "UTC"):
    async with _pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO schedules (telegram_id, hour, minute, timezone, active)
            VALUES ($1, $2, $3, $4, TRUE)
            ON CONFLICT (telegram_id) DO UPDATE
            SET hour = $2, minute = $3, timezone = $4, active = TRUE
        ''', telegram_id, hour, minute, timezone)

async def delete_schedule(telegram_id: int):
    async with _pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM schedules WHERE telegram_id = $1", telegram_id
        )

async def get_all_active_schedules():
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM schedules WHERE active = TRUE"
        )
        return [dict(row) for row in rows]


# ── Retry Queue ───────────────────────────────────────────────────────────────

RETRYABLE_STATUSES = {"TIMEOUT", "ERROR", "FAILED", "SKIPPED_SLOW"}
# Statuses that are permanently unrecoverable — never re-queue these
DEAD_STATUSES = {"DEAD", "NO_FORM", "CAPTCHA", "BLOCKED", "MISSING_INFORMATION"}
RETRY_COOLDOWN_MINUTES = 30   # wait 30 min before first retry
MAX_RETRY_ATTEMPTS = 3

async def enqueue_retry(telegram_id: int, url: str, attempt: int = 1):
    """Add a URL to the retry queue with a cooldown delay."""
    import datetime
    retry_after = datetime.datetime.utcnow() + datetime.timedelta(
        minutes=RETRY_COOLDOWN_MINUTES * attempt  # back off with each attempt
    )
    async with _pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO retry_queue (telegram_id, url, attempt, retry_after)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (telegram_id, url) DO UPDATE
            SET attempt = $3, retry_after = $4
        ''', telegram_id, url, attempt, retry_after)

async def get_due_retries(telegram_id: int):
    """Return URLs whose retry_after time has passed."""
    async with _pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT id, url, attempt FROM retry_queue
            WHERE telegram_id = $1 AND retry_after <= NOW()
            ORDER BY retry_after
        ''', telegram_id)
        return [dict(row) for row in rows]

async def remove_retry(telegram_id: int, url: str):
    async with _pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM retry_queue WHERE telegram_id = $1 AND url = $2",
            telegram_id, url
        )

async def get_retry_queue_count(telegram_id: int):
    async with _pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM retry_queue WHERE telegram_id = $1", telegram_id
        )


# ── Dashboard ─────────────────────────────────────────────────────────────────

async def purge_dead_retries() -> int:
    """Remove retry queue entries for URLs whose last known status is permanently unrecoverable.
    Returns the count of entries removed."""
    async with _pool.acquire() as conn:
        result = await conn.execute('''
            DELETE FROM retry_queue rq
            USING results r
            WHERE rq.telegram_id = r.telegram_id
              AND rq.url = r.url
              AND r.status = ANY($1::text[])
              AND r.id = (
                  SELECT MAX(id) FROM results r2
                  WHERE r2.telegram_id = rq.telegram_id AND r2.url = rq.url
              )
        ''', list(DEAD_STATUSES))
        # result is like "DELETE 42"
        try:
            return int(result.split()[-1])
        except Exception:
            return 0
    """Return distinct telegram_ids that have at least one due retry entry."""
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT telegram_id FROM retry_queue WHERE retry_after <= NOW()"
        )
    return [row["telegram_id"] for row in rows]


async def db_heartbeat() -> None:
    """Cheapest possible query — verifies the DB connection is alive."""
    async with _pool.acquire() as conn:
        await conn.fetchval("SELECT 1")


async def get_dashboard_stats(telegram_id: int) -> dict:
    """Single query bundle that powers the user dashboard."""
    async with _pool.acquire() as conn:
        # All-time totals per status
        all_time = await conn.fetch('''
            SELECT status, COUNT(*) AS count
            FROM results WHERE telegram_id = $1
            GROUP BY status
        ''', telegram_id)

        # Today's totals (UTC)
        today = await conn.fetch('''
            SELECT status, COUNT(*) AS count
            FROM results
            WHERE telegram_id = $1
              AND created_at >= CURRENT_DATE
            GROUP BY status
        ''', telegram_id)

        # Last run timestamp
        last_run = await conn.fetchval('''
            SELECT MAX(created_at) FROM results WHERE telegram_id = $1
        ''', telegram_id)

        # Website count
        website_count = await conn.fetchval(
            "SELECT COUNT(*) FROM websites WHERE telegram_id = $1", telegram_id
        )

        # Retry queue pending
        retry_pending = await conn.fetchval(
            "SELECT COUNT(*) FROM retry_queue WHERE telegram_id = $1", telegram_id
        )

        # Schedule
        schedule = await conn.fetchrow(
            "SELECT hour, minute, active FROM schedules WHERE telegram_id = $1", telegram_id
        )

        return {
            "all_time":     {r["status"]: r["count"] for r in all_time},
            "today":        {r["status"]: r["count"] for r in today},
            "last_run":     last_run,
            "website_count": website_count or 0,
            "retry_pending": retry_pending or 0,
            "schedule":     dict(schedule) if schedule else None,
        }
