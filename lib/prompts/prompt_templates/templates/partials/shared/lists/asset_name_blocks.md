---
protected: true
---
<characters>
{% for name in character_names %}
- {{ name }}
{% else %}
（暂无）
{% endfor %}
</characters>

<scenes>
{% for name in scene_names %}
- {{ name }}
{% else %}
（暂无）
{% endfor %}
</scenes>

<props>
{% for name in prop_names %}
- {{ name }}
{% else %}
（暂无）
{% endfor %}
</props>