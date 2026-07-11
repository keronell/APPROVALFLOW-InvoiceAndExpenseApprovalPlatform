// N1: every API call carries the self-signed role JWT fetched at sign-in.
let authToken = "";

export function setAuthToken(token) {
  authToken = token;
}

export async function jsonFetch(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
  const resp = await fetch(url, { ...options, headers });
  if (!resp.ok) {
    const error = new Error(`${resp.status} ${await resp.text()}`);
    error.status = resp.status;
    throw error;
  }
  return resp.json();
}
