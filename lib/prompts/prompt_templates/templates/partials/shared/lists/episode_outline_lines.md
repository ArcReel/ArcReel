{% if outline.story_beats %}
故事节点：
{% for beat in outline.story_beats %}
- {{ beat }}
{% endfor %}
{% endif %}
{% if outline.hook %}
集尾钩子：{{ outline.hook }}
{% endif %}
{% if outline.next_episode_teaser %}
下集预告语：{{ outline.next_episode_teaser }}
{% endif %}