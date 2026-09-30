# Example

Schema: `{invoice_number: string, total: number, currency: string}`

```json
{
  "record": {"invoice_number": "INV-2041", "total": 1180.5, "currency": "GBP"},
  "evidence": {"total": {"span": "Total due: £1,180.50", "location": "p1"}},
  "warnings": []
}
```
