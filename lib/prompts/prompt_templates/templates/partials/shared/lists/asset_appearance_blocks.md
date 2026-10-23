---
protected: true
---
<characters>
{% for entry in assets.characters %}
{% if entry.appearance %}
- {{ entry.name }}：{{ entry.appearance | indent(2, blank=True) }}{% if entry.voice_style %}｜声音：{{ entry.voice_style }}{% endif %}

{% else %}
- {{ entry.name }}{% if entry.voice_style %}｜声音：{{ entry.voice_style }}{% endif %}

{% endif %}
{% else %}
（暂无）
{% endfor %}
</characters>

<scenes>
{% for entry in assets.scenes %}
{% if entry.appearance %}
- {{ entry.name }}：{{ entry.appearance | indent(2, blank=True) }}
{% else %}
- {{ entry.name }}
{% endif %}
{% else %}
（暂无）
{% endfor %}
</scenes>

<props>
{% for entry in assets.props %}
{% if entry.appearance %}
- {{ entry.name }}：{{ entry.appearance | indent(2, blank=True) }}
{% else %}
- {{ entry.name }}
{% endif %}
{% else %}
（暂无）
{% endfor %}
</props>