// fork-private — Users management section embedded in Settings page.
//
// Listing + creation + delete. No password edits (single shared
// AUTH_PASSWORD this commit). Admin-only — returns null for non-admins.

import { type FormEvent, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { getToken } from '@/utils/auth';
import { errMsg, voidPromise } from '@/utils/async';
import { isAdmin, usePermissionsStore } from '@/stores/fork-permissions-store';

interface UserRow {
  id: string;
  username: string;
  role: string;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

async function authFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const token = getToken();
  const headers = new Headers(init.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (!headers.has('Content-Type') && init.body) headers.set('Content-Type', 'application/json');
  return fetch(`/api/v1${path}`, { ...init, headers });
}

export function ForkUsersSection() {
  const { t } = useTranslation(['fork', 'common']);
  const role = usePermissionsStore((s) => s.role);
  const loaded = usePermissionsStore((s) => s.loaded);
  const [users, setUsers] = useState<UserRow[]>([]);
  const [username, setUsername] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!loaded || !isAdmin(role)) return;
    void (async () => {
      const resp = await authFetch('/users');
      if (resp.ok) setUsers((await resp.json()) as UserRow[]);
    })();
  }, [loaded, role]);

  if (!loaded || !isAdmin(role)) return null;

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault();
    setError('');
    setSubmitting(true);
    try {
      const resp = await authFetch('/users', {
        method: 'POST',
        body: JSON.stringify({ username: username.trim(), role: 'user' }),
      });
      if (!resp.ok) {
        const body = (await resp.json().catch(() => ({}))) as { detail?: string };
        throw new Error(body.detail ?? `HTTP ${resp.status}`);
      }
      const created = (await resp.json()) as UserRow;
      setUsers((prev) => [...prev, created]);
      setUsername('');
    } catch (err) {
      setError(errMsg(err, 'Failed to create user'));
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (row: UserRow) => {
    if (!globalThis.confirm(t('fork:access.users.delete_confirm', { name: row.username }))) return;
    const resp = await authFetch(`/users/${encodeURIComponent(row.id)}`, { method: 'DELETE' });
    if (resp.ok) {
      setUsers((prev) => prev.filter((u) => u.id !== row.id));
    }
  };

  return (
    <div className="space-y-6 p-6">
      <header>
        <h2 className="text-lg font-semibold text-text">{t('fork:access.users.title')}</h2>
      </header>

      <p className="rounded-lg border border-amber-800/50 bg-amber-900/20 p-3 text-sm text-amber-200">
        {t('fork:access.users.shared_password_hint')}
      </p>

      <section className="rounded-xl border border-gray-800 bg-gray-900 p-5">
        <h3 className="mb-3 text-sm font-medium text-gray-300">{t('fork:access.users.create')}</h3>
        <form className="flex gap-2" onSubmit={voidPromise(handleCreate)}>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder={t('fork:access.users.username_placeholder')}
            className="flex-1 rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm outline-none focus:border-indigo-500"
            required
          />
          <button
            type="submit"
            disabled={submitting || !username.trim()}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {t('fork:access.users.create_submit')}
          </button>
        </form>
        {error && <p className="mt-2 text-sm text-red-400">{error}</p>}
      </section>

      <section className="overflow-hidden rounded-xl border border-gray-800 bg-gray-900">
        {users.length === 0 ? (
          <p className="p-6 text-sm text-gray-500">{t('fork:access.users.empty')}</p>
        ) : (
          <table className="w-full text-left text-sm">
            <thead className="bg-gray-800/50 text-xs uppercase text-gray-400">
              <tr>
                <th className="px-4 py-2">{t('fork:access.users.column_username')}</th>
                <th className="px-4 py-2">{t('fork:access.users.column_role')}</th>
                <th className="px-4 py-2">{t('fork:access.users.column_active')}</th>
                <th className="px-4 py-2">{t('fork:access.users.column_created')}</th>
                <th className="px-4 py-2 text-right">{t('fork:access.users.column_actions')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {users.map((u) => (
                <tr key={u.id}>
                  <td className="px-4 py-2 font-mono">{u.username}</td>
                  <td className="px-4 py-2">{t(`fork:access.role.${u.role}`, { defaultValue: u.role })}</td>
                  <td className="px-4 py-2">{u.is_active ? '✓' : '—'}</td>
                  <td className="px-4 py-2 text-gray-500">{u.created_at?.slice(0, 19).replace('T', ' ') ?? ''}</td>
                  <td className="px-4 py-2 text-right">
                    <button
                      type="button"
                      onClick={voidPromise(() => handleDelete(u))}
                      className="text-xs text-red-400 hover:text-red-300"
                    >
                      {t('fork:access.users.delete')}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
