// JSON API client.

async function call(method, path, body) {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* empty body */
  }
  if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
  return data;
}

function qs(params = {}) {
  const p = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '');
  return p.length ? '?' + new URLSearchParams(p).toString() : '';
}

export const api = {
  get: (p, params) => call('GET', p + qs(params)),
  post: (p, b) => call('POST', p, b),
  put: (p, b) => call('PUT', p, b),
  del: (p) => call('DELETE', p),
};

/** Company-scoped helpers bound to the active company. */
export function companyApi(cid) {
  const base = `/companies/${cid}`;
  return {
    list: (kind, params) => api.get(`${base}/${kind}`, params),
    get: (kind, id) => api.get(`${base}/${kind}/${id}`),
    create: (kind, body) => api.post(`${base}/${kind}`, body),
    update: (kind, id, body) => api.put(`${base}/${kind}/${id}`, body),
    remove: (kind, id) => api.del(`${base}/${kind}/${id}`),
    report: (name, params) => api.get(`${base}/reports/${name}`, params),
    previewInvoice: (body) => api.post(`${base}/invoice-preview`, body),
    setBankDate: (entryId, bankDate) => api.put(`${base}/bank-entries/${entryId}`, { bank_date: bankDate }),
    editLog: (params) => api.get(`${base}/edit-log`, params),
  };
}
