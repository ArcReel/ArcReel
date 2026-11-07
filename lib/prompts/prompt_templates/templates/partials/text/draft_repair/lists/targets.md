{% for target in targets %}
## `{{ root }}[{{ target.index }}]`

Violations:
{% for message in target.messages %}
- {{ message }}
{% endfor %}

Current content:

```json
{{ target.item_json }}
```

{% endfor %}
