/** Read API responses without exposing HTML error pages as JSON parser failures. */
export async function requestJSON(url, options = {}, fetcher = fetch) {
  let response;
  try {
    response = await fetcher(url, {cache: 'no-store', ...options});
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('Could not reach the local server. Check that it is running, then try again.');
  }
  let body;
  try {
    body = await response.text();
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new Error('The local server response was interrupted. Please try again.');
  }
  let payload;
  try {
    payload = JSON.parse(body);
  } catch {
    if (response.status === 404) {
      throw new Error('This endpoint is unavailable. Restart the local server and refresh the page.');
    }
    throw new Error(`The local server returned an unexpected response (HTTP ${response.status}). Restart it and try again.`);
  }
  if (!response.ok) {
    if (response.status === 404 && (!payload?.error || payload.error === 'Not Found')) {
      throw new Error('This endpoint is unavailable. Restart the local server and refresh the page.');
    }
    throw new Error(typeof payload?.error === 'string' ? payload.error : `Request failed (HTTP ${response.status}).`);
  }
  return payload;
}
