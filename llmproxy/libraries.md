
Library bits for reference/aide-memoir

- httpx.AsyncClient is an async HTTP client from the httpx library — essentially an async equivalent of requests.Session
  - Async/await — all methods (get, post, stream, etc.) are coroutines, so they work with asyncio
  - Connection pooling — reuses TCP connections when used as a context manager or long-lived instance
  - Streaming support — client.stream() for SSE/chunked responses without buffering the full body
  - API similar to requests — familiar interface (response.status_code, .json(), .headers, etc.)