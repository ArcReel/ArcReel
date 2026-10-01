---
protected: true
---
<characters>
{% for entry in assets.characters %}
- {{ entry.name }}{% if entry.aliases %}（别名：{{ entry.aliases | join("、") }}）{% endif %}{% if entry.appearance %}：{{ entry.appearance | indent(2, blank=True) }}{% endif %}

{% else %}
（暂无）
{% endfor %}
</characters>

<scenes>
{% for entry in assets.scenes %}
- {{ entry.name }}{% if entry.aliases %}（别名：{{ entry.aliases | join("、") }}）{% endif %}{% if entry.appearance %}：{{ entry.appearance | indent(2, blank=True) }}{% endif %}

{% else %}
（暂无）
{% endfor %}
</scenes>

<props>
{% for entry in assets.props %}
- {{ entry.name }}{% if entry.aliases %}（别名：{{ entry.aliases | join("、") }}）{% endif %}{% if entry.appearance %}：{{ entry.appearance | indent(2, blank=True) }}{% endif %}

{% else %}
（暂无）
{% endfor %}
</props>