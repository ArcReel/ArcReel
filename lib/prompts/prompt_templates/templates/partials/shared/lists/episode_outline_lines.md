{% if outline.story_beats %}
Story beats:
{% for beat in outline.story_beats %}
- {{ beat }}
{% endfor %}
{% endif %}
{% if outline.hook %}
Episode-end hook: {{ outline.hook }}
{% endif %}
{% if outline.next_episode_teaser %}
Next-episode teaser: {{ outline.next_episode_teaser }}
{% endif %}
