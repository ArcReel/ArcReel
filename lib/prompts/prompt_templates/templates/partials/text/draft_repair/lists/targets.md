{% for target in targets %}
## `{{ root }}[{{ target.index }}]`

违约：
{% for message in target.messages %}
- {{ message }}
{% endfor %}

当前内容：

```json
{{ target.item_json }}
```

{% endfor %}
