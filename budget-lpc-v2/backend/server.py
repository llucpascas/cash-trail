"""Budget LPC v2. Local-only API and structured, integer-money ledger."""
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from imports import cents, clean, normalized, parse_date, preview
import auth

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get('BUDGET_LPC_DB', str(ROOT / 'data' / 'budget-lpc-v2.sqlite')))
CURRENCIES = ['EUR', 'AED', 'SCR', 'USD', 'GBP', 'CHF', 'SEK', 'NOK', 'DKK', 'PLN', 'CAD', 'AUD', 'SGD']
COLORS = ['#477c70', '#b28550', '#6987ab', '#a56e72', '#9b9360', '#7b749b', '#668b97', '#aa806b']
PREVIEWS = {}


@contextmanager
def database():
    url = os.environ.get('BUDGET_LPC_DATABASE_URL')
    if url:
        from postgres import Database
        connection = Database(url, os.environ.get('BUDGET_LPC_SCHEMA', 'budget_lpc'))
    else:
        connection = sqlite3.connect(DB_PATH, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize():
    if os.environ.get('BUDGET_LPC_REQUIRE_REMOTE_DB') == '1' and not os.environ.get('BUDGET_LPC_DATABASE_URL'):
        raise RuntimeError('Free hosting requires the permanent Budget LPC database connection.')
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with database() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS accounts(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, bank TEXT NOT NULL DEFAULT '', currency TEXT NOT NULL, profile TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, parent_id INTEGER REFERENCES categories(id), color TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS batches(id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id), filename TEXT NOT NULL, file_hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, imported INTEGER NOT NULL, duplicates INTEGER NOT NULL, start TEXT, end TEXT, coverage_confirmed INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS transactions(id INTEGER PRIMARY KEY, date TEXT NOT NULL, description TEXT NOT NULL, cents INTEGER NOT NULL, currency TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, category_id INTEGER REFERENCES categories(id), account_id INTEGER REFERENCES accounts(id), label TEXT NOT NULL DEFAULT '', expense_type TEXT NOT NULL DEFAULT 'variable', source TEXT NOT NULL, source_key TEXT UNIQUE, batch_id INTEGER REFERENCES batches(id), notes TEXT NOT NULL DEFAULT '', suggestion TEXT, matched_to_id INTEGER REFERENCES transactions(id), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE INDEX IF NOT EXISTS transaction_period ON transactions(currency,date,status);
        CREATE TABLE IF NOT EXISTS bank_keys(account_id INTEGER NOT NULL REFERENCES accounts(id), fingerprint TEXT NOT NULL, transaction_id INTEGER NOT NULL REFERENCES transactions(id), PRIMARY KEY(account_id,fingerprint));
        CREATE TABLE IF NOT EXISTS budgets(month TEXT NOT NULL,currency TEXT NOT NULL,income_cents INTEGER NOT NULL DEFAULT 0,investment_cents INTEGER NOT NULL DEFAULT 0,limits TEXT NOT NULL DEFAULT '{}',PRIMARY KEY(month,currency));
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        ''')
        if 'match_original_account_id' not in {r[1] for r in db.execute('PRAGMA table_info(transactions)')}:
            db.execute('ALTER TABLE transactions ADD COLUMN match_original_account_id INTEGER')
        for i, name in enumerate(['Food', 'Home', 'Utilities', 'Transport', 'Health', 'Plans with Friends', 'Travel', 'Shopping', 'Other']):
            db.execute('INSERT OR IGNORE INTO categories(name,color) VALUES (?,?)', (name, COLORS[i % len(COLORS)]))
        db.execute("INSERT OR IGNORE INTO accounts(name,bank,currency) VALUES ('Cash / manual','Manual','EUR')")


@asynccontextmanager
async def lifespan(app):
    initialize()
    auth.initialize(database)
    yield


app = FastAPI(title='Budget LPC', lifespan=lifespan)
auth.install(app, database)


@app.middleware('http')
async def local_requests(request, call_next):
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        allowed_origin = os.environ.get('BUDGET_LPC_PUBLIC_URL', '').rstrip('/') or f'{request.url.scheme}://{request.url.netloc}'
        if origin and origin != allowed_origin:
            return JSONResponse({'detail': 'Only requests from this app are accepted'}, status_code=403)
    response = await call_next(request)
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    elif request.url.path in ('/', '/index.html', '/sw.js', '/manifest.webmanifest', '/offline.html'):
        response.headers['Cache-Control'] = 'no-cache'
    if request.url.path == '/sw.js':
        response.headers['Service-Worker-Allowed'] = '/'
    if request.url.path == '/manifest.webmanifest':
        response.headers['Content-Type'] = 'application/manifest+json'
    return response


def valid_currency(currency):
    if currency not in CURRENCIES:
        raise HTTPException(400, 'Choose a supported currency')
    return currency


def valid_month(month):
    if not re.fullmatch(r'\d{4}-\d{2}', month):
        raise HTTPException(400, 'Month must be YYYY-MM')
    try:
        date.fromisoformat(month + '-01')
    except ValueError:
        raise HTTPException(400, 'Invalid month')
    return month


def category_exists(db, category_id):
    if category_id is not None and not db.execute('SELECT 1 FROM categories WHERE id=?', (category_id,)).fetchone():
        raise HTTPException(400, 'Category does not exist')


@app.get('/api/health')
def health():
    return {'app': 'budget-lpc-v2', 'version': 2}


@app.get('/api/settings')
def settings():
    with database() as db:
        migration = db.execute("SELECT value FROM meta WHERE key='migration_report'").fetchone()
        return {'hosted': auth.enabled(), 'currencies': CURRENCIES, 'accounts': [dict(r) | {'profile': json.loads(r['profile'])} for r in db.execute('SELECT * FROM accounts ORDER BY id')],
                'categories': [dict(r) for r in db.execute('SELECT * FROM categories ORDER BY name')],
                'migration': json.loads(migration[0]) if migration else None,
                'latest_month': db.execute("SELECT MAX(substr(date,1,7)) FROM transactions WHERE status='approved'").fetchone()[0]}


class AccountInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    bank: str = Field(default='', max_length=80)
    currency: str = 'EUR'


@app.post('/api/accounts')
def add_account(body: AccountInput):
    valid_currency(body.currency)
    if not body.name.strip():
        raise HTTPException(400, 'Enter an account name')
    with database() as db:
        try:
            account_id = db.execute('INSERT INTO accounts(name,bank,currency) VALUES (?,?,?)', (body.name.strip(), body.bank.strip(), body.currency)).lastrowid
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'An account with this name already exists')
    return {'id': account_id}


class CategoryInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    parent_id: int | None = None


@app.post('/api/categories')
def add_category(body: CategoryInput):
    with database() as db:
        category_exists(db, body.parent_id)
        if not body.name.strip():
            raise HTTPException(400, 'Enter a category name')
        try:
            result = db.execute('INSERT INTO categories(name,parent_id,color) VALUES (?,?,?)', (body.name.strip(), body.parent_id, COLORS[db.execute('SELECT COUNT(*) FROM categories').fetchone()[0] % len(COLORS)])).lastrowid
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'This category already exists')
    return {'id': result}


@app.put('/api/categories/{category_id}')
def update_category(category_id: int, body: CategoryInput):
    with database() as db:
        category_exists(db, category_id)
        category_exists(db, body.parent_id)
        parent = body.parent_id
        while parent:
            if parent == category_id:
                raise HTTPException(400, 'A category cannot be inside itself or its children')
            parent = db.execute('SELECT parent_id FROM categories WHERE id=?', (parent,)).fetchone()[0]
        try:
            if not body.name.strip():
                raise HTTPException(400, 'Enter a category name')
            db.execute('UPDATE categories SET name=?,parent_id=? WHERE id=?', (body.name.strip(), body.parent_id, category_id))
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'This category name already exists')
    return {'ok': True}


class Entry(BaseModel):
    date: date
    description: str = Field(min_length=1, max_length=500)
    amount: str
    currency: str = 'EUR'
    kind: Literal['income', 'expense', 'refund', 'transfer', 'investment']
    category_id: int | None = None
    account_id: int
    label: str = Field(default='', max_length=80)
    expense_type: Literal['fixed', 'variable'] = 'variable'
    notes: str = Field(default='', max_length=2000)


def entry_values(body, db):
    valid_currency(body.currency)
    category_exists(db, body.category_id)
    if not db.execute('SELECT 1 FROM accounts WHERE id=?', (body.account_id,)).fetchone():
        raise HTTPException(400, 'Choose an account')
    try:
        amount = cents(body.amount)
    except ValueError as error:
        raise HTTPException(400, str(error))
    if amount == 0 or not body.description.strip():
        raise HTTPException(400, 'Enter a description and a non-zero amount')
    if body.kind != 'transfer':
        amount = abs(amount) * (-1 if body.kind in ('expense', 'investment') else 1)
    return (body.date.isoformat(), body.description.strip(), amount, body.currency, body.kind,
            body.category_id, body.account_id, body.label.strip(), body.expense_type, body.notes)


@app.post('/api/transactions')
def create_entry(body: Entry):
    with database() as db:
        values = entry_values(body, db)
        txid = db.execute("INSERT INTO transactions(date,description,cents,currency,kind,category_id,account_id,label,expense_type,notes,status,source) VALUES (?,?,?,?,?,?,?,?,?,?,'approved','manual')", values).lastrowid
    return {'id': txid}


@app.put('/api/transactions/{txid}')
def edit_entry(txid: int, body: Entry):
    with database() as db:
        existing = db.execute('SELECT * FROM transactions WHERE id=?', (txid,)).fetchone()
        if not existing:
            raise HTTPException(404, 'Transaction not found')
        if existing['source'] == 'bank' or existing['status'] == 'matched':
            raise HTTPException(400, 'Use review to change bank transaction classifications; original bank amounts are preserved')
        if db.execute('SELECT 1 FROM transactions WHERE matched_to_id=?', (txid,)).fetchone():
            raise HTTPException(400, 'This record is matched to a bank transaction; its amount and date are locked')
        db.execute('UPDATE transactions SET date=?,description=?,cents=?,currency=?,kind=?,category_id=?,account_id=?,label=?,expense_type=?,notes=? WHERE id=?', (*entry_values(body, db), txid))
    return {'ok': True}


TX_SELECT = 'SELECT t.*, c.name AS category, c.color, a.name AS account FROM transactions t LEFT JOIN categories c ON c.id=t.category_id LEFT JOIN accounts a ON a.id=t.account_id'


@app.get('/api/transactions')
def transactions(month: str = '', currency: str = '', status: str = '', kind: str = '', search: str = '', category_id: int | None = None):
    conditions, params = [], []
    if month:
        conditions.append('substr(t.date,1,7)=?'); params.append(valid_month(month))
    if currency:
        conditions.append('t.currency=?'); params.append(valid_currency(currency))
    if status:
        conditions.append('t.status=?'); params.append(status)
    else:
        conditions.append("t.status!='matched'")
    if kind:
        conditions.append('t.kind=?'); params.append(kind)
    if category_id:
        conditions.append('t.category_id=?'); params.append(category_id)
    if search:
        conditions.append("(t.description LIKE ? OR t.label LIKE ? OR c.name LIKE ?)"); params.extend(['%' + search + '%'] * 3)
    with database() as db:
        rows = [dict(r) for r in db.execute(TX_SELECT + (' WHERE ' + ' AND '.join(conditions) if conditions else '') + ' ORDER BY t.date DESC,t.id DESC', params)]
        for row in rows:
            row['suggestion'] = json.loads(row['suggestion']) if row['suggestion'] else None
            row['matches'] = []
            if row['status'] == 'pending' and row['source'] == 'bank':
                row['matches'] = [dict(r) for r in db.execute("SELECT m.id,m.date,m.description,m.kind FROM transactions m WHERE m.source IN ('manual','legacy') AND m.status='approved' AND m.cents=? AND m.currency=? AND ABS(julianday(m.date)-julianday(?))<=3 AND NOT EXISTS(SELECT 1 FROM transactions x WHERE x.matched_to_id=m.id) ORDER BY m.date DESC", (row['cents'], row['currency'], row['date']))]
    return rows


class Review(BaseModel):
    kind: Literal['income', 'expense', 'refund', 'transfer', 'investment']
    category_id: int | None = None
    label: str = Field(default='', max_length=80)
    expense_type: Literal['fixed', 'variable'] = 'variable'
    action: Literal['approve', 'exclude', 'restore', 'match', 'unmatch'] = 'approve'
    match_id: int | None = None


class BulkReview(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)
    category_id: int | None = None
    kind: Literal['income', 'expense', 'refund', 'transfer', 'investment'] | None = None
    expense_type: Literal['fixed', 'variable'] = 'variable'


@app.post('/api/review/approve-batch')
def approve_batch(body: BulkReview):
    ids = list(dict.fromkeys(body.ids))
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        category_exists(db, body.category_id)
        rows = db.execute('SELECT * FROM transactions WHERE id IN (' + ','.join('?' for _ in ids) + ')', ids).fetchall()
        if len(rows) != len(ids) or any(row['status'] != 'pending' for row in rows):
            raise HTTPException(409, 'Some selected transactions are no longer pending. Refresh and select again.')
        for row in rows:
            kind = body.kind or row['kind']
            category = body.category_id or row['category_id']
            if (kind in ('income','refund') and row['cents'] < 0) or (kind in ('expense','investment') and row['cents'] > 0):
                raise HTTPException(400, 'The selected type conflicts with at least one amount. Select only money-in or only money-out rows.')
            if kind in ('expense','refund') and category is None:
                raise HTTPException(400, 'Choose a category for the selected expenses or refunds')
            if db.execute("SELECT 1 FROM transactions m WHERE m.source IN ('manual','legacy') AND m.status='approved' AND m.cents=? AND m.currency=? AND ABS(julianday(m.date)-julianday(?))<=3 AND NOT EXISTS(SELECT 1 FROM transactions x WHERE x.matched_to_id=m.id)", (row['cents'],row['currency'],row['date'])).fetchone():
                raise HTTPException(400, 'A selected transaction has a possible existing manual entry. Review that match individually first.')
            db.execute("UPDATE transactions SET status='approved',kind=?,category_id=?,expense_type=? WHERE id=?", (kind,category,body.expense_type,row['id']))
    return {'approved': len(rows)}


@app.post('/api/transactions/{txid}/review')
def review_entry(txid: int, body: Review):
    with database() as db:
        row = db.execute('SELECT * FROM transactions WHERE id=?', (txid,)).fetchone()
        if not row:
            raise HTTPException(404, 'Transaction not found')
        if body.action == 'unmatch':
            if row['status'] != 'matched':
                raise HTTPException(400, 'This transaction is not matched')
            if row['match_original_account_id'] is not None:
                db.execute('UPDATE transactions SET account_id=? WHERE id=?', (row['match_original_account_id'], row['matched_to_id']))
            db.execute("UPDATE transactions SET status='pending',matched_to_id=NULL,match_original_account_id=NULL WHERE id=?", (txid,))
            return {'ok': True}
        if row['status'] == 'matched':
            raise HTTPException(400, 'This bank row has already been matched')
        category_exists(db, body.category_id)
        if body.action == 'match':
            match = db.execute("SELECT * FROM transactions WHERE id=? AND source IN ('manual','legacy') AND status='approved'", (body.match_id,)).fetchone()
            if not match or match['cents'] != row['cents'] or match['currency'] != row['currency'] or abs((date.fromisoformat(match['date'])-date.fromisoformat(row['date'])).days)>3:
                raise HTTPException(400, 'Match must have the same currency and amount and be within three days')
            if row['source'] != 'bank' or row['status'] != 'pending' or db.execute('SELECT 1 FROM transactions WHERE matched_to_id=?', (body.match_id,)).fetchone():
                raise HTTPException(409, 'One of these records is already matched')
            db.execute("UPDATE transactions SET status='matched',matched_to_id=?,match_original_account_id=? WHERE id=?", (body.match_id, match['account_id'], txid))
            db.execute('UPDATE transactions SET account_id=? WHERE id=?', (row['account_id'], body.match_id))
        elif body.action == 'exclude':
            if db.execute('SELECT 1 FROM transactions WHERE matched_to_id=?', (txid,)).fetchone():
                raise HTTPException(400, 'A matched manual record cannot be excluded')
            db.execute("UPDATE transactions SET status='excluded' WHERE id=?", (txid,))
        elif body.action == 'restore':
            db.execute("UPDATE transactions SET status='pending' WHERE id=?", (txid,))
        else:
            if body.kind in ('income', 'refund') and row['cents'] < 0 or body.kind in ('expense', 'investment') and row['cents'] > 0:
                raise HTTPException(400, 'This classification conflicts with the bank amount. Money in is income/refund; money out is expense/investment.')
            if body.kind in ('expense', 'refund') and body.category_id is None:
                raise HTTPException(400, 'Choose a category for this expense or refund')
            db.execute("UPDATE transactions SET status='approved',kind=?,category_id=?,label=?,expense_type=? WHERE id=?", (body.kind, body.category_id, body.label.strip(), body.expense_type, txid))
    return {'ok': True}


@app.post('/api/imports/preview')
async def import_preview(file: UploadFile = File(...), options: str = Form('{}')):
    content = await file.read(10_000_001)
    if len(content) > 10_000_000:
        raise HTTPException(400, 'Limit: 10 MB per statement')
    try:
        config = json.loads(options)
        with database() as db:
            account = db.execute('SELECT * FROM accounts WHERE id=?', (int(config.get('account_id', 0)),)).fetchone()
            if not account:
                raise ValueError('Choose or create an account first')
            config['currency'] = account['currency']
            result = preview(file.filename or 'statement', content, config)
            for row in result['rows']:
                row['duplicate'] = bool(db.execute('SELECT 1 FROM bank_keys WHERE account_id=? AND fingerprint=?', (account['id'], row['fingerprint'])).fetchone())
        token = uuid.uuid4().hex
        for old in list(PREVIEWS):
            if time.time() - PREVIEWS[old]['at'] > 1800:
                del PREVIEWS[old]
        if len(PREVIEWS) >= 20:
            del PREVIEWS[next(iter(PREVIEWS))]
        PREVIEWS[token] = {'at': time.time(), 'result': result, 'config': config, 'hash': hashlib.sha256(content).hexdigest()}
        result = dict(result)
        result['token'] = token
        result['duplicates'] = sum(row['duplicate'] for row in result['rows'])
        result['total'] = len(result['rows'])
        result['rows'] = result['rows'][:100]
        return result
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise HTTPException(400, str(error))


class CommitImport(BaseModel):
    token: str
    coverage_start: date | None = None
    coverage_end: date | None = None


@app.post('/api/imports/commit')
def commit_import(body: CommitImport):
    cached = PREVIEWS.get(body.token)
    if not cached or time.time() - cached['at'] > 1800:
        raise HTTPException(400, 'Preview expired. Preview the file again.')
    result, config = cached['result'], cached['config']
    if result.get('mapping_required') or result['errors'] or not result['rows']:
        raise HTTPException(400, 'Resolve the preview errors before importing')
    if bool(body.coverage_start) != bool(body.coverage_end):
        raise HTTPException(400, 'Provide both statement coverage dates, or leave both blank')
    start, end = result['start'], result['end']
    if body.coverage_start:
        if body.coverage_start > body.coverage_end or body.coverage_start.isoformat() > start or body.coverage_end.isoformat() < end:
            raise HTTPException(400, 'Coverage dates must contain all transaction dates')
        start, end = body.coverage_start.isoformat(), body.coverage_end.isoformat()
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        account_id = int(config['account_id'])
        fresh = [r for r in result['rows'] if not db.execute('SELECT 1 FROM bank_keys WHERE account_id=? AND fingerprint=?', (account_id, r['fingerprint'])).fetchone()]
        duplicates = len(result['rows']) - len(fresh)
        batch_id = db.execute('INSERT INTO batches(account_id,filename,file_hash,imported,duplicates,start,end,coverage_confirmed) VALUES (?,?,?,?,?,?,?,?)', (account_id, result['filename'], cached['hash'], len(fresh), duplicates, start, end, bool(body.coverage_start))).lastrowid
        for row in fresh:
            text = normalized(row['description'])
            kind = 'expense' if row['cents'] < 0 else 'income'
            if any(word in text for word in ['refund', 'devolucion', 'devolucio', 'reembolso']):
                kind = 'refund' if row['cents'] > 0 else 'expense'
            # A transfer-like description is only a review suggestion, never an automatic exclusion.
            if any(word in text for word in ['transfer', 'traspaso']):
                kind = 'transfer'
            txid = db.execute("INSERT INTO transactions(date,description,cents,currency,kind,status,account_id,source,batch_id) VALUES (?,?,?,?,?,'pending',?,'bank',?)", (row['date'], row['description'], row['cents'], row['currency'], kind, account_id, batch_id)).lastrowid
            db.execute('INSERT INTO bank_keys VALUES (?,?,?)', (account_id, row['fingerprint'], txid))
        saved = {k: v for k, v in config.items() if k in ('mapping', 'header', 'date_order', 'decimal', 'invert')}
        saved['mapping'], saved['header'] = result['mapping'], result['header']
        db.execute('UPDATE accounts SET profile=? WHERE id=?', (json.dumps(saved), account_id))
    PREVIEWS.pop(body.token, None)
    return {'imported': len(fresh), 'duplicates': duplicates, 'id': batch_id}


@app.get('/api/imports')
def import_history():
    with database() as db:
        batches = [dict(r) for r in db.execute('SELECT b.*,a.name AS account FROM batches b JOIN accounts a ON a.id=b.account_id ORDER BY b.id DESC')]
    ranges = defaultdict(list)
    for batch in batches:
        if batch['coverage_confirmed']:
            ranges[batch['account']].append((batch['start'], batch['end']))
    gaps = []
    for account, periods in ranges.items():
        end = None
        for start, stop in sorted(periods):
            if end and date.fromisoformat(start) > date.fromisoformat(end) + timedelta(days=1):
                gaps.append({'account': account, 'start': (date.fromisoformat(end) + timedelta(days=1)).isoformat(), 'end': (date.fromisoformat(start) - timedelta(days=1)).isoformat()})
            end = max(stop, end or stop)
    return {'batches': batches, 'gaps': gaps}


class Budget(BaseModel):
    month: str
    currency: str
    income: str = '0'
    investment: str = '0'
    limits: dict[str, str] = Field(default_factory=dict)


@app.put('/api/budget')
def save_budget(body: Budget):
    valid_month(body.month); valid_currency(body.currency)
    try:
        income, investment = cents(body.income or '0'), cents(body.investment or '0')
        limits = {str(int(k)): cents(v or '0') for k, v in body.limits.items()}
        if min([income, investment, *limits.values()]) < 0:
            raise ValueError('Budget amounts cannot be negative')
    except ValueError as error:
        raise HTTPException(400, str(error))
    with database() as db:
        for key in limits:
            category_exists(db, int(key))
        db.execute('INSERT INTO budgets VALUES (?,?,?,?,?) ON CONFLICT(month,currency) DO UPDATE SET income_cents=excluded.income_cents,investment_cents=excluded.investment_cents,limits=excluded.limits', (body.month, body.currency, income, investment, json.dumps(limits)))
    return {'ok': True}


@app.get('/api/budget')
def get_budget(month: str, currency: str = 'EUR'):
    valid_month(month); valid_currency(currency)
    with database() as db:
        row = db.execute('SELECT * FROM budgets WHERE month=? AND currency=?', (month, currency)).fetchone()
        previous = db.execute('SELECT month FROM budgets WHERE month<? AND currency=? ORDER BY month DESC LIMIT 1', (month, currency)).fetchone()
        return (dict(row) | {'limits': json.loads(row['limits'])} if row else {'month': month, 'currency': currency, 'income_cents': 0, 'investment_cents': 0, 'limits': {}}) | {'previous': previous[0] if previous else None}


@app.get('/api/dashboard')
def dashboard(month: str, currency: str = 'EUR'):
    valid_month(month); valid_currency(currency)
    with database() as db:
        records = [dict(r) for r in db.execute(TX_SELECT + " WHERE t.currency=? AND t.status='approved' ORDER BY t.date DESC,t.id DESC", (currency,))]
        categories = {r['id']: dict(r) for r in db.execute('SELECT * FROM categories')}
        pending = db.execute("SELECT COUNT(*),COALESCE(SUM(ABS(cents)),0) FROM transactions WHERE currency=? AND status='pending' AND substr(date,1,7)=?", (currency, month)).fetchone()
        all_pending = db.execute("SELECT COUNT(*) FROM transactions WHERE status='pending'").fetchone()[0]
    selected = [r for r in records if r['date'].startswith(month)]
    totals = {'income': 0, 'expenses': 0, 'investments': 0, 'fixed': 0, 'variable': 0}
    year = month[:4]
    monthly = {f'{year}-{m:02d}': {'month': f'{year}-{m:02d}', 'income': 0, 'expenses': 0, 'savings': 0} for m in range(1,13)}
    grouped, direct = defaultdict(int), defaultdict(int)
    for row in selected:
        if row['kind'] == 'income':
            totals['income'] += row['cents']
        elif row['kind'] in ('expense', 'refund'):
            spent = -row['cents']
            totals['expenses'] += spent
            totals[row['expense_type']] += spent
            category_id = row['category_id']
            direct[str(category_id)] += spent
            seen = set()
            while category_id in categories and categories[category_id]['parent_id'] and category_id not in seen:
                seen.add(category_id)
                category_id = categories[category_id]['parent_id']
            grouped[category_id] += spent
        elif row['kind'] == 'investment':
            totals['investments'] -= row['cents']
    for row in records:
        key = row['date'][:7]
        if key in monthly:
            if row['kind'] == 'income': monthly[key]['income'] += row['cents'] / 100
            if row['kind'] in ('expense','refund'): monthly[key]['expenses'] -= row['cents'] / 100
    for item in monthly.values(): item['savings'] = item['income'] - item['expenses']
    totals['savings'] = totals['income'] - totals['expenses']
    totals['after_investments'] = totals['savings'] - totals['investments']
    totals['savings_rate'] = totals['savings'] / totals['income'] * 100 if totals['income'] else None
    category_totals = [{'id': key, 'name': categories[key]['name'] if key in categories else 'Uncategorized', 'color': categories[key]['color'] if key in categories else '#94948c', 'cents': value} for key,value in grouped.items()]
    return {'month': month, 'currency': currency, 'totals': totals, 'monthly': list(monthly.values()), 'categories': sorted(category_totals, key=lambda x: -x['cents']), 'direct_categories': dict(direct), 'recent': selected[:8], 'pending': pending[0], 'pending_cents': pending[1], 'all_pending': all_pending}


@app.get('/api/export')
def export_csv():
    with database() as db:
        rows = db.execute(TX_SELECT + ' ORDER BY t.date,t.id').fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    fields = ['id','date','description','amount','currency','kind','status','category','account','label','expense_type','source','matched_to_id','notes']
    writer.writerow(fields)
    for row in rows:
        data = dict(row); data['amount'] = f"{data['cents']/100:.2f}"
        writer.writerow([("'" + str(data.get(k) or '')) if k not in ('id','amount','matched_to_id') and str(data.get(k) or '').startswith(('=','+','-','@')) else data.get(k, '') for k in fields])
    return Response('\ufeff' + output.getvalue(), media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="budget-lpc-transactions.csv"'})


@app.get('/api/backup')
def backup():
    handle, path = tempfile.mkstemp(suffix='.sqlite'); os.close(handle)
    with database() as source:
        target = sqlite3.connect(path)
        source.backup(target); target.close()
    return FileResponse(path, filename=f'budget-lpc-{date.today()}.sqlite', background=BackgroundTask(lambda: Path(path).unlink(missing_ok=True)))


class AIRequest(BaseModel):
    ids: list[int] = Field(max_length=10, min_length=1)
    consent: Literal[True]


@app.post('/api/review/suggest')
def ai_suggest(body: AIRequest):
    # Only this explicit action may call Codex; imports and the dashboard never do.
    import importlib.util
    import sys
    source = ROOT.parent / 'cash-trail-source' / 'IkerJansa44-cash-trail-7dea90e' / 'backend'
    if not source.exists():
        raise HTTPException(503, 'AI suggestions are not configured on this server. Statement importing and manual review are available.')
    if str(source) not in sys.path: sys.path.append(str(source))
    from app.categorizer import classify_transactions
    with database() as db:
        rows = [dict(r) for r in db.execute('SELECT * FROM transactions WHERE id IN (' + ','.join('?' for _ in body.ids) + ") AND status='pending' AND kind IN ('expense','refund')", body.ids)]
        categories = [r[0] for r in db.execute('SELECT name FROM categories')]
    if not rows:
        raise HTTPException(400, 'Select pending expenses or refunds for suggestions')
    payload = [{'key': str(r['id']), 'description': r['description'], 'amount': r['cents']/100, 'currency': r['currency']} for r in rows]
    results = classify_transactions(payload, categories, {})
    if not results:
        raise HTTPException(503, 'Codex did not return suggestions. Check your Codex CLI sign-in or try again; manual review is always available.')
    with database() as db:
        count = 0
        for key, result in results.items():
            if key not in {str(r['id']) for r in rows}: continue
            db.execute("UPDATE transactions SET suggestion=? WHERE id=? AND status='pending'", (json.dumps({'category': result.category, 'description': result.summary, 'confidence': result.confidence}), int(key)))
            count += 1
    return {'suggested': count}


@app.get('/api/{path:path}')
def unknown_api(path: str):
    raise HTTPException(404, 'API route not found')


if (ROOT / 'frontend' / 'dist').exists():
    app.mount('/', StaticFiles(directory=ROOT / 'frontend' / 'dist', html=True))

