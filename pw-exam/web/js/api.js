// API client: bearer-token auth, JSON, file downloads, network-error detection.
import { downloadBlob, store } from './util.js';

const TOKEN_KEY = 'pwexam.session';
let session = store.get(TOKEN_KEY);
let onAuthLost = () => {};

export class NetworkError extends Error {}

export const auth = {
  get session() {
    return session;
  },
  get user() {
    return session?.user || null;
  },
  set(s) {
    session = s;
    store.set(TOKEN_KEY, s);
  },
  clear() {
    session = null;
    store.del(TOKEN_KEY);
  },
  onLost(fn) {
    onAuthLost = fn;
  },
};

async function request(method, path, body, { raw = false } = {}) {
  const headers = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (session?.token) headers.Authorization = `Bearer ${session.token}`;
  let res;
  try {
    res = await fetch(`/api${path}`, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined, cache: 'no-store' });
  } catch (e) {
    throw new NetworkError('You appear to be offline');
  }
  if (raw && res.ok) return res;
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* no body */
  }
  if (res.status === 401 && session && !path.startsWith('/auth/login') && !path.startsWith('/auth/verify-otp')) {
    auth.clear();
    onAuthLost(data?.error);
  }
  if (!res.ok) {
    const err = new Error(data?.error || `Request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return data;
}

export const api = {
  get: (p) => request('GET', p),
  post: (p, b = {}) => request('POST', p, b),
  put: (p, b = {}) => request('PUT', p, b),
  del: (p) => request('DELETE', p),
  async download(path, filename) {
    const res = await request('GET', path, undefined, { raw: true });
    downloadBlob(await res.blob(), filename);
  },
};
