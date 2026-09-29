# Example finding

**Blocking - `src/paginate.py:42`** - loop bound `range(0, total + 1, size)` yields an extra empty page when `total % size == 0`.
*Failing scenario:* `total=20,size=10` returns 3 pages; the last request is out of range and the API returns 400.
*Suggested fix:* `range(0, total, size)`.
