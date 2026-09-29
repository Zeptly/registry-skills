# Example: licence lookup

**Input:** `question: "What licence is the Python 'requests' library released under?"`

**Expected shape of the report:**

```
Answer: Apache-2.0.
Evidence:
| Claim | Source | Date | Quote |
| Released under Apache-2.0 | https://github.com/psf/requests | (retrieved) | "Apache 2.0" |
| Licence classifier on PyPI | https://pypi.org/project/requests/ | (retrieved) | "Apache Software License" |
Conflicts and gaps: none.
Confidence: high - primary repository and package index agree.
```
